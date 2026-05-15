"""``RegimeOrchestrator`` — wires detector + tracker into the
SafetyLayer and the persistence layer.

The orchestrator is the single seam between the pure regime/ core and
the rest of the runtime. The main loop calls ``observe(bars, at,
snapshot_id)`` once per tick boundary; the orchestrator:

  1. Asks ``RegimeDetector.evaluate(bars)`` for the current regime.
  2. Feeds the result into ``TransitionTracker.observe(regime, at)``.
  3. If a confirmed ``TransitionEvent`` is emitted, builds a
     ``KillSwitchTrigger(STRATEGY, "regime_transition", DEGRADE)``,
     calls ``safety.raise_trigger`` (REQ_F_RGM_005 / REQ_SDD_RGM_004),
     and persists the event through ``TransitionRepository``
     (REQ_SDD_RGM_005).

The detector + tracker stay pure; the orchestrator owns the side
effects. The trade-execution path is unaffected — a transition never
calls ``KillSwitch.set_state`` directly; only the SafetyLayer can do
so (REQ_S_KS_002).

REQ refs: REQ_F_RGM_005, REQ_SDS_RGM_001, REQ_SDD_RGM_004,
REQ_SDD_RGM_005.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from trading_system.data.types import Bar
from trading_system.models.identifiers import (
    DEFAULT_ACCOUNT_ID,
    AccountId,
    SnapshotId,
)
from trading_system.models.phase import MarketRegime
from trading_system.models.safety import KillSwitchTrigger, TriggerCategory
from trading_system.persistence.repositories.transition import TransitionRepository
from trading_system.regime.detector import RegimeDetector
from trading_system.regime.transition import TransitionTracker
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.safety.protocol import SafetyLayer


@dataclass(slots=True)
class RegimeTick:
    """Public result of one orchestrator tick."""

    regime: MarketRegime
    transition_raised: bool


@dataclass(slots=True)
class RegimeOrchestrator:
    """Detector + tracker + SafetyLayer + persistence wiring.

    The orchestrator is a thin glue layer — every action delegates to
    the underlying components. Operators construct it once at startup
    (after rehydrating the tracker from the persistence layer); the
    main loop calls ``observe`` per tick boundary.
    """

    detector: RegimeDetector
    tracker: TransitionTracker
    safety: SafetyLayer
    repo: TransitionRepository
    account_id: AccountId = DEFAULT_ACCOUNT_ID

    def observe(
        self,
        bars: Sequence[Bar],
        *,
        at: datetime,
        snapshot_id: SnapshotId,
    ) -> Result[RegimeTick, str]:
        """One tick: classify, track, and (on a confirmed transition)
        raise the trigger + persist the event.

        ``snapshot_id`` is pre-staged by the caller — the orchestrator
        does NOT write the audit snapshot itself (the state manager
        owns that artifact). The same pattern is used by the risk
        engine (see ``risk/engine.py``).
        """
        match self.detector.evaluate(bars):
            case Err(reason):
                return Err(reason)
            case Ok(regime):
                pass

        transition = self.tracker.observe(regime, at=at)
        match transition:
            case Nothing():
                return Ok(RegimeTick(regime=regime, transition_raised=False))
            case Some(event):
                # 1. Raise the trigger through the SafetyLayer.
                #    KILL is reserved for irrecoverable conditions per
                #    REQ_S_KS_002; regime transitions are recoverable
                #    so the severity is always DEGRADE
                #    (REQ_F_RGM_005).
                trigger = KillSwitchTrigger(
                    category=TriggerCategory.STRATEGY,
                    code="regime_transition",
                    message=(
                        f"regime transitioned from {event.from_regime.value} "
                        f"to {event.to_regime.value}"
                    ),
                    severity="DEGRADE",
                    raised_at=at,
                    snapshot_id=snapshot_id,
                )
                self.safety.raise_trigger(trigger)
                # 2. Persist the event for restart rehydration +
                #    operator-tooling history (REQ_SDD_RGM_005).
                match self.repo.append(
                    event,
                    snapshot_id=snapshot_id,
                    account_id=self.account_id,
                ):
                    case Err(reason):
                        # Persistence failure does NOT roll back the
                        # SafetyLayer trigger — the operator is already
                        # informed via the kill-switch state machine.
                        # We surface the persistence reason so the
                        # caller can structured-log it.
                        return Err(reason)
                    case Ok(_):
                        pass
                return Ok(RegimeTick(regime=regime, transition_raised=True))

    def current_regime(self) -> Option[MarketRegime]:
        """Read-only accessor — the tracker's current cursor regime
        (None when the tracker hasn't observed any bars yet)."""
        return self.tracker.current_regime
