"""``TransitionTracker`` — confirmed regime transitions.

Carries a single mutable cursor ``(_current, _candidate,
_candidate_count)`` and emits ``TransitionEvent`` only after the new
regime persists for ``confirmation_periods`` consecutive observations
(REQ_F_RGM_004 / REQ_SDD_RGM_003). A regime that flips back before
reaching the confirmation threshold SHALL NOT emit a transition — the
candidate window resets.

REQ refs: REQ_F_RGM_004, REQ_F_RGM_005, REQ_SDS_RGM_002,
REQ_SDD_RGM_003, REQ_NF_RGM_001.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_system.models.phase import MarketRegime, TransitionEvent
from trading_system.result import Nothing, Option, Some

# ``TransitionEvent`` now lives in ``trading_system.models.phase`` so
# the regime + persistence layers can both depend on the model
# without creating a package import cycle (REQ_SDD_IMP_003). The
# re-export here preserves the existing
# ``from trading_system.regime.transition import TransitionEvent``
# call sites — no caller needs to update.
__all__ = ["TransitionEvent", "TransitionTracker"]


@dataclass(slots=True)
class TransitionTracker:
    """Single mutable cursor over ``(_current, _candidate, _count)``.

    The only mutable element of the ``regime/`` package
    (REQ_SDS_RGM_002). Construct via ``TransitionTracker(confirmation_periods=...)``;
    seed via ``from_persistence`` when restarting against an existing
    transition history.
    """

    confirmation_periods: int
    _current: MarketRegime | None = field(default=None)
    _candidate: MarketRegime | None = field(default=None)
    _candidate_count: int = field(default=0)

    def __post_init__(self) -> None:
        if self.confirmation_periods < 1:
            raise ValueError(
                "TransitionTracker.confirmation_periods must be >= 1, "
                f"got {self.confirmation_periods}"
            )

    @classmethod
    def from_seed(
        cls,
        *,
        confirmation_periods: int,
        current: MarketRegime,
    ) -> "TransitionTracker":
        """Construct a tracker pre-seeded with the operator's known
        current regime — used after a restart when the latest
        persisted transition's ``to_regime`` is the truth on disk
        (REQ_SDD_RGM_005)."""
        return cls(
            confirmation_periods=confirmation_periods,
            _current=current,
            _candidate=None,
            _candidate_count=0,
        )

    @property
    def current_regime(self) -> Option[MarketRegime]:
        """Read-only view of the cursor's current regime."""
        if self._current is None:
            return Nothing()
        return Some(self._current)

    def observe(self, regime: MarketRegime, at: datetime) -> Option[TransitionEvent]:
        """Consume the next ``(regime, at)`` observation; return
        ``Some(TransitionEvent)`` only when the candidate window is
        full, otherwise ``Nothing()``."""
        if self._current is None:
            # First observation: seed the cursor; no transition.
            self._current = regime
            self._candidate = None
            self._candidate_count = 0
            return Nothing()

        if regime == self._current:
            # Stable: reset the candidate window (REQ_SDD_RGM_003).
            self._candidate = None
            self._candidate_count = 0
            return Nothing()

        # Different from current — accumulate or start a candidate.
        if self._candidate == regime:
            self._candidate_count += 1
        else:
            self._candidate = regime
            self._candidate_count = 1

        if self._candidate_count < self.confirmation_periods:
            return Nothing()

        event = TransitionEvent(
            from_regime=self._current,
            to_regime=regime,
            at=at,
            confirmation_periods=self.confirmation_periods,
        )
        self._current = regime
        self._candidate = None
        self._candidate_count = 0
        return Some(event)
