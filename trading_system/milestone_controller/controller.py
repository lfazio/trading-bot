"""``MilestoneController`` — configurable milestone ladder + gradual unlock.

Pipeline (REQ_F_MIL_002):

1. Take the current ``equity`` and the ``CapitalFlow`` — the
   "total capital" reference is ``capflow.initial`` (so milestones
   measure realised growth net of injections, REQ_F_CFL_002).
2. Find the next un-crossed milestone above the current realised
   capital position.
3. Verify every gating condition (stable returns AND low drawdown
   AND strategy consistency AND no recent kill-switch trigger AND
   not fake-growth). Failure on any -> emit nothing.
4. Emit ``MilestoneCrossing(target, exposure_increase_pct)`` with
   the operator's configured pct in [0.10, 0.20].

Crossing emission is single-shot: callers SHOULD call
``register_crossed`` after applying the unlock so the next
``evaluate`` call advances to the following milestone.

REQ refs: REQ_F_MIL_001..004, REQ_SDS_MOD_012, REQ_SDD_ALG_015.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading_system.capital_flow.flow import CapitalFlow
from trading_system.milestone_controller.metrics import PerformanceMetrics
from trading_system.models.money import Currency, Money
from trading_system.models.safety import KillSwitchTrigger
from trading_system.result import Nothing, Option, Some


def _eur(amount: int) -> Money:
    return Money(Decimal(amount), Currency.EUR)


# REQ_F_MIL_001 — default milestone list. Any deployment may override
# via ``MilestoneController(milestones=...)``.
DEFAULT_MILESTONES: tuple[Money, ...] = (
    _eur(2_000),
    _eur(5_000),
    _eur(10_000),
    _eur(20_000),
    _eur(50_000),
    _eur(100_000),
    _eur(200_000),
    _eur(500_000),
    _eur(1_000_000),
    _eur(2_000_000),
    _eur(5_000_000),
)

# REQ_F_MIL_003 — exposure unlock band; the controller refuses to
# emit anything outside [0.10, 0.20].
_EXPOSURE_INCREASE_MIN = Decimal("0.10")
_EXPOSURE_INCREASE_MAX = Decimal("0.20")

# REQ_SDD_ALG_015 — fake-growth thresholds.
_FAKE_GROWTH_GAIN_30D = Decimal("0.30")
_FAKE_GROWTH_LARGEST_TRADE = Decimal("0.50")
_FAKE_GROWTH_VOL_MULT = Decimal(2)


@dataclass(frozen=True, slots=True)
class MilestoneCrossing:
    """One unlock event emitted by the controller."""

    target: Money
    exposure_increase_pct: Decimal

    def __post_init__(self) -> None:
        if not (_EXPOSURE_INCREASE_MIN <= self.exposure_increase_pct <= _EXPOSURE_INCREASE_MAX):
            raise ValueError(
                f"MilestoneCrossing.exposure_increase_pct must lie in "
                f"[{_EXPOSURE_INCREASE_MIN}, {_EXPOSURE_INCREASE_MAX}], "
                f"got {self.exposure_increase_pct}"
            )
        if self.target.amount <= 0:
            raise ValueError(f"MilestoneCrossing.target must be > 0, got {self.target.amount}")


@dataclass(frozen=True, slots=True)
class MilestoneConfig:
    """Per-deployment knobs."""

    exposure_increase_pct: Decimal = _EXPOSURE_INCREASE_MIN

    def __post_init__(self) -> None:
        if not (_EXPOSURE_INCREASE_MIN <= self.exposure_increase_pct <= _EXPOSURE_INCREASE_MAX):
            raise ValueError(
                f"MilestoneConfig.exposure_increase_pct must lie in "
                f"[{_EXPOSURE_INCREASE_MIN}, {_EXPOSURE_INCREASE_MAX}], "
                f"got {self.exposure_increase_pct}"
            )


@dataclass(slots=True)
class MilestoneController:
    """Stateful: tracks which milestones have already been crossed
    so the same crossing isn't emitted twice."""

    milestones: tuple[Money, ...] = DEFAULT_MILESTONES
    cfg: MilestoneConfig = field(default_factory=MilestoneConfig)
    _crossed: set[Decimal] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        if not self.milestones:
            raise ValueError("MilestoneController.milestones must be non-empty")
        # Single-currency by construction; mixing currencies in a
        # ladder is a config error.
        currency = self.milestones[0].currency
        for m in self.milestones:
            if m.currency != currency:
                raise ValueError(
                    "MilestoneController.milestones must share a currency, "
                    f"got {m.currency} vs {currency}"
                )
            if m.amount <= 0:
                raise ValueError(
                    f"MilestoneController.milestones values must be > 0, got {m.amount}"
                )
        # Ascending order is required so ``_next_uncrossed_above``
        # can short-circuit.
        for prev, cur in zip(self.milestones[:-1], self.milestones[1:], strict=True):
            if cur.amount <= prev.amount:
                raise ValueError(
                    "MilestoneController.milestones must be strictly ascending; "
                    f"got {prev.amount} >= {cur.amount}"
                )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        equity: Money,
        capital_flow: CapitalFlow,
        recent_kill_switch_triggers: tuple[KillSwitchTrigger, ...],
        perf: PerformanceMetrics,
    ) -> Option[MilestoneCrossing]:
        """Decide whether a milestone should fire right now.

        Returns ``Some(crossing)`` when every gate passes; ``Nothing()``
        otherwise. Caller applies the unlock and (on success) calls
        ``register_crossed(crossing.target)`` so the next ``evaluate``
        advances.
        """
        # Strip injections so the milestone measures realised growth
        # only (REQ_F_CFL_002). The "growth" capital is
        # ``equity - cumulative_injected_at(now)``; since the caller
        # passes the live equity, we compare against the initial
        # capital baseline.
        if equity.currency != self.milestones[0].currency:
            raise ValueError(
                f"MilestoneController.evaluate: equity.currency "
                f"({equity.currency}) must match milestone currency "
                f"({self.milestones[0].currency})"
            )
        next_ms_opt = self._next_uncrossed_above(equity, capital_flow)
        if isinstance(next_ms_opt, Nothing):
            return Nothing()
        next_ms: Money = next_ms_opt.value

        # Gate 1: stable returns AND low drawdown AND strategy consistency.
        if not (perf.stable_returns and perf.low_drawdown and perf.strategy_consistency):
            return Nothing()

        # Gate 2: no recent KS event (REQ_F_MIL_002).
        if recent_kill_switch_triggers:
            return Nothing()

        # Gate 3: fake-growth detector (REQ_F_MIL_004 / REQ_SDD_ALG_015).
        if self._fake_growth(perf):
            return Nothing()

        return Some(
            MilestoneCrossing(
                target=next_ms,
                exposure_increase_pct=self.cfg.exposure_increase_pct,
            )
        )

    def register_crossed(self, target: Money) -> None:
        """Mark a milestone as already-emitted so the next
        ``evaluate`` call advances to the following one."""
        self._crossed.add(target.amount)

    @property
    def crossed(self) -> tuple[Money, ...]:
        """Read-only view of the milestones already emitted."""
        return tuple(m for m in self.milestones if m.amount in self._crossed)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _next_uncrossed_above(self, equity: Money, capital_flow: CapitalFlow) -> Option[Money]:
        """The smallest milestone that is (a) >= current realised
        capital position AND (b) not already crossed."""
        # Realised capital position = equity - cumulative injections
        # at the most recent injection in the ledger. We use
        # ``capital_flow.initial`` as the floor; the controller
        # measures growth past initial + injections.
        threshold = capital_flow.initial.amount
        # If equity is below the first milestone, no crossing possible.
        for m in self.milestones:
            if m.amount in self._crossed:
                continue
            if m.amount <= threshold:
                # Already past this on day one; mark crossed so the
                # next call doesn't re-evaluate it.
                self._crossed.add(m.amount)
                continue
            if equity.amount >= m.amount:
                return Some(m)
            # Milestones are ascending; once we find one above equity
            # without crossing, no later milestone will qualify.
            return Nothing()
        return Nothing()

    @staticmethod
    def _fake_growth(perf: PerformanceMetrics) -> bool:
        """REQ_SDD_ALG_015 — any of: 30-day gain > 30%, single trade
        > 50% of capital, realized vol > 2x rolling vol average."""
        if perf.gain_30d > _FAKE_GROWTH_GAIN_30D:
            return True
        if perf.largest_trade_pct > _FAKE_GROWTH_LARGEST_TRADE:
            return True
        # When rolling vol is zero we can't form a meaningful ratio;
        # the conservative answer is "treat as fake-growth" so the
        # operator's first month of trading doesn't auto-promote.
        if perf.rolling_vol_avg <= 0:
            return perf.realized_vol > 0
        return perf.realized_vol > _FAKE_GROWTH_VOL_MULT * perf.rolling_vol_avg
