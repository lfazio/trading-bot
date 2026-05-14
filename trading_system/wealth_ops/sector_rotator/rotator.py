"""``SectorRotator`` — Phase-5+ regime-driven sector tilt.

Pipeline (REQ_F_SCT_001..007 / REQ_SDD_SCT_002..007):

1. Phase guard: short-circuit to ``()`` if ``phase < Phase.FIVE``.
   The implementation MUST NOT consult the regime / screener /
   holding-state in this branch (REQ_SDS_SCT_002 / REQ_SDD_SCT_002).
2. Bias lookup: if the regime has no row in the bias table, emit
   ``()`` silently — the operator hasn't configured this regime.
3. Compute the current sector-weight vector from the screener
   ranking (treat top-N by score; for v1 we take all scored
   stocks weighted equally per sector).
4. Taxonomy validation: any sector emitted by the screener that
   is absent from the taxonomy drops the entire cycle
   (REQ_F_SCT_005 / REQ_SDD_SCT_003).
5. Whipsaw dampener: update the regime episode; if the cumulative
   direction-changes in this episode exceed the policy, drop
   (REQ_F_SCT_004 / REQ_SDD_SCT_006).
6. Quarter cap: roll over on a calendar-quarter boundary; if the
   per-quarter cap is reached, drop (REQ_F_SCT_006 / REQ_SDD_SCT_005).
7. Holding-period gate: every sector flagged for exit (target
   weight < current weight) must have been held at least
   ``policy.min_holding_days`` days. If any flagged sector falls
   short, drop the whole cycle (REQ_F_SCT_003 / REQ_SDD_SCT_007).
8. Emit a single ``RotationProposal`` with full provenance and
   advance the cursor (single-writer per REQ_SDS_SCT_003).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.models.meta import RotationProposal
from trading_system.models.phase import MarketRegime, Phase
from trading_system.screener.engine import ScoredStock
from trading_system.wealth_ops.sector_rotator.policy import (
    HoldingState,
    RotationPolicy,
)
from trading_system.wealth_ops.sector_rotator.regime_sector_bias import (
    RegimeSectorBias,
)
from trading_system.wealth_ops.sector_rotator.taxonomy import SectorTaxonomy

# Quarters of the year — used by the rotation-cap rollover
# (REQ_SDD_SCT_005). January / April / July / October starts.
_QUARTER_STARTS = (1, 4, 7, 10)


@dataclass(slots=True)
class SectorRotator:
    """Stateful Phase-5+ rotation engine.

    Construction parameters:
    - ``bias`` — frozen regime-to-sector-weight table (REQ_F_SCT_002).
    - ``taxonomy`` — operator-supplied sector vocabulary
      (REQ_F_SCT_005).
    - ``policy`` — frozen knobs (REQ_F_SCT_003 / 004 / 006).
    - ``state`` — the single mutable HoldingState cursor
      (REQ_SDS_SCT_003 / REQ_SDD_SCT_005).
    - ``policy_id`` — opaque identifier carried on every emitted
      proposal for audit replay (REQ_F_SCT_007).
    """

    bias: RegimeSectorBias
    taxonomy: SectorTaxonomy
    policy: RotationPolicy
    state: HoldingState
    policy_id: str

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("SectorRotator.policy_id must be non-empty")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(  # noqa: PLR0911, PLR0912 — gate-ordered; one branch per gate by design
        self,
        *,
        phase: Phase,
        regime: MarketRegime,
        screener_ranking: tuple[ScoredStock, ...],
        at: datetime,
    ) -> tuple[RotationProposal, ...]:
        """Run the pipeline; emit at most one proposal."""
        # Step 1 — phase guard (REQ_SDD_SCT_002).
        if phase.value < Phase.FIVE.value:
            return ()

        # Step 2 — regime row lookup.
        target = self.bias.weights_for(regime)
        if target is None:
            return ()

        # Step 3 — current sector weights from the screener ranking.
        # If the operator has nothing in the universe yet, no
        # rotation can fire.
        if not screener_ranking:
            return ()
        current = _current_sector_weights(screener_ranking)

        # Step 4 — taxonomy validation. Both source AND target must
        # be in the operator's vocabulary; an unknown sector
        # emitted by the screener drops the cycle.
        for sector in current:
            verdict = self.taxonomy.validate(sector)
            if verdict.is_err():
                return ()
        for sector in target:
            verdict = self.taxonomy.validate(sector)
            if verdict.is_err():
                return ()

        # Step 5 — whipsaw dampener (REQ_SDD_SCT_006).
        # Update the regime episode; if we re-flipped within the
        # same episode beyond the dampener, drop.
        flipped_in_episode = self._update_regime_episode(regime, at, current, target)
        if flipped_in_episode > self.policy.whipsaw_dampener:
            return ()

        # Step 6 — quarter rollover + rotation-cap (REQ_SDD_SCT_005).
        self._roll_quarter(at)
        if self.state.rotations_this_quarter >= self.policy.max_rotations_per_quarter:
            return ()

        # Step 7 — holding-period guard (REQ_SDD_SCT_007).
        for sector, current_w in current.items():
            target_w = target.get(sector, Decimal(0))
            if target_w >= current_w:
                continue  # not flagged for exit
            entered_at = self.state.last_entry.get(sector)
            if entered_at is None:
                # Never recorded as entered — treat as eligible.
                continue
            held_days = (at - entered_at).days
            if held_days < self.policy.min_holding_days:
                return ()

        # Step 8 — emit + cursor advance.
        proposal = RotationProposal(
            source_regime=regime,
            source_weights=dict(current),
            dest_weights=dict(target),
            decided_at=at,
            policy_id=self.policy_id,
        )
        self._record_rotation(current, target, at)
        return (proposal,)

    # ------------------------------------------------------------------
    # Internals — single-writer mutations on ``self.state``
    # ------------------------------------------------------------------

    def _update_regime_episode(
        self,
        regime: MarketRegime,
        at: datetime,
        current: dict[str, Decimal],
        target: dict[str, Decimal],
    ) -> int:
        """Maintain the regime-episode tuple + direction-change
        counter. Returns the post-update counter so the caller can
        compare against the policy."""
        prev = self.state.regime_episode
        if prev is None or prev[0] != regime:
            # New episode (REQ_SDD_SCT_006). Reset both fields.
            self.state.regime_episode = (regime, at)
            self.state.direction_changes_in_episode = 0
            return 0
        # Same regime episode — check whether the rotation flips
        # direction relative to the *current* weights. We define a
        # direction change as: at least one sector's target sign
        # (relative to current) differs from the dominant direction
        # established in this episode. v1 simplification: treat any
        # net change in the rotation set as a direction change.
        if _has_meaningful_diff(current, target):
            self.state.direction_changes_in_episode += 1
        return self.state.direction_changes_in_episode

    def _roll_quarter(self, at: datetime) -> None:
        """Reset ``rotations_this_quarter`` on a quarter boundary."""
        if self.state.quarter_started_at is None:
            self.state.quarter_started_at = _quarter_start(at)
            return
        cur_quarter = _quarter_start(at)
        if cur_quarter != self.state.quarter_started_at:
            self.state.quarter_started_at = cur_quarter
            self.state.rotations_this_quarter = 0

    def _record_rotation(
        self,
        current: dict[str, Decimal],
        target: dict[str, Decimal],
        at: datetime,
    ) -> None:
        """Update last_entry / last_exit + rotations counter."""
        self.state.rotations_this_quarter += 1
        for sector, w in target.items():
            cur_w = current.get(sector, Decimal(0))
            if w > cur_w:
                self.state.last_entry[sector] = at
            elif w < cur_w:
                self.state.last_exit[sector] = at


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _current_sector_weights(
    screener_ranking: tuple[ScoredStock, ...],
) -> dict[str, Decimal]:
    """Aggregate the screener output into a sector-weight vector.

    v1 simplification: each scored stock contributes equally to its
    sector's weight; weights are then normalised so they sum to 1
    (matching the bias-table convention). Future iterations can
    weight by score or market cap.
    """
    counts: dict[str, int] = {}
    for s in screener_ranking:
        sector = s.stock.sector
        counts[sector] = counts.get(sector, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {sector: Decimal(c) / Decimal(total) for sector, c in counts.items()}


def _has_meaningful_diff(current: dict[str, Decimal], target: dict[str, Decimal]) -> bool:
    """``True`` iff any sector's weight differs by more than 1e-6."""
    sectors = set(current) | set(target)
    threshold = Decimal("1e-6")
    for s in sectors:
        diff = abs(current.get(s, Decimal(0)) - target.get(s, Decimal(0)))
        if diff > threshold:
            return True
    return False


def _quarter_start(at: datetime) -> datetime:
    """Return the start-of-quarter datetime for ``at``."""
    month = at.month
    for q_start in _QUARTER_STARTS:
        if month < q_start:
            # Previous quarter
            prev = _QUARTER_STARTS[_QUARTER_STARTS.index(q_start) - 1]
            return at.replace(month=prev, day=1, hour=0, minute=0, second=0, microsecond=0)
        if month == q_start or (q_start == _QUARTER_STARTS[-1] and month >= q_start):
            return at.replace(month=q_start, day=1, hour=0, minute=0, second=0, microsecond=0)
    return at.replace(month=10, day=1, hour=0, minute=0, second=0, microsecond=0)
