"""``StateManager`` — concrete ``SafetyLayer`` implementation.

Single writer for ``KillSwitchState`` (REQ_SDS_MOD_010); every
transition writes an audit snapshot (REQ_NF_AUD_001) and fans the
event out to the configured ``AlertChannel`` set (REQ_S_KS_007).
Trigger thresholds are loaded once at construction and stored as
frozen instance fields — runtime mutation is unreachable
(REQ_S_KS_010 / REQ_SDS_CFG_003 / REQ_SDD_API_004).

State-transition rules:

- Any trigger with severity ``KILL`` advances to ``KILL`` (terminal
  until manual recovery).
- A trigger with severity ``DEGRADE`` advances ``ACTIVE`` ->
  ``DEGRADED``; once already ``DEGRADED`` or ``KILL``, the trigger
  is recorded but the state does not regress.
- ``request_recovery`` requires (a) a valid operator token, (b) all
  four recovery conditions per REQ_S_KS_009. Successful recovery
  returns to ``ACTIVE`` and writes a ``RECOVERY``-severity snapshot.

REQ refs: REQ_S_KS_001..012, REQ_SDS_MOD_010, REQ_SDD_API_002,
REQ_SDD_API_003 (must_halt is O(1), no I/O), REQ_SDD_LOG_002
(KS event log fields).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from typing import Any

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.notifications.fanout import NotificationFanOut
from trading_system.notifications.payloads import (
    KillSwitchEvent,
    KillSwitchSeverity,
)
from trading_system.result import Err, Ok, Result
from trading_system.safety.alerts import AlertChannel, deliver_with_retry
from trading_system.safety.recovery import (
    OperatorTokenVerifier,
    RecoveryConditions,
)
from trading_system.safety.snapshot import AuditSnapshot, SnapshotSink


@dataclass(frozen=True, slots=True)
class StateManagerConfig:
    """Immutable trigger / behavior configuration.

    The fields here cover the policy choices the SDD documents but
    that don't quite belong in `risk.yaml` or `kill_switch.yaml`:
    they describe *how* the state manager reacts, not *what* the
    monitors should look for.
    """

    snapshot_id_prefix: str = "snap"
    require_manual_recovery: bool = True


@dataclass(slots=True)
class StateManager:
    """Concrete ``SafetyLayer``.

    Construct with explicit dependencies — verifier, snapshot sink,
    alert channels, and an injectable ``now`` callable for tests.
    """

    verifier: OperatorTokenVerifier
    snapshot_sink: SnapshotSink
    alert_channels: list[AlertChannel] = field(default_factory=list)
    cfg: StateManagerConfig = field(default_factory=StateManagerConfig)
    # CR-001 Phase B bridge — optional. When set, every KS state
    # transition (including idempotent same-state DEGRADE / KILL
    # entries) dispatches a typed ``KillSwitchEvent`` through the
    # new notification fan-out *after* the legacy AlertChannel path
    # so REQ_F_NOT_003 / REQ_SDD_NOT_002 land without breaking
    # existing KS-only deployments (which leave this field None).
    notification_fanout: NotificationFanOut | None = None

    _state: KillSwitchState = field(default=KillSwitchState.ACTIVE, init=False)
    _seq: count[int] = field(default_factory=lambda: count(1), init=False)
    _last_trigger: KillSwitchTrigger | None = field(default=None, init=False)
    _frozen_runtime: bool = field(default=True, init=False)

    # ------------------------------------------------------------------
    # SafetyLayer Protocol
    # ------------------------------------------------------------------

    def must_halt(self) -> bool:
        """REQ_SDD_API_003: O(1), no I/O, no locks."""
        return self._state is KillSwitchState.KILL

    def state(self) -> KillSwitchState:
        return self._state

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        """Apply ``trigger`` to the state machine.

        Severity ``KILL`` -> ``KILL`` (terminal until recovery).
        Severity ``DEGRADE`` -> ``DEGRADED`` from ``ACTIVE``;
        a no-op when state is already ``DEGRADED`` or ``KILL`` (we
        record the trigger and emit a snapshot, but the state does
        not regress).
        """
        target = self._target_state_for(trigger)
        if target is self._state and trigger.severity == "DEGRADE":
            # Idempotent: same-state DEGRADE recorded but no transition.
            self._record_event(self._state, self._state, trigger)
            return
        if target is self._state and trigger.severity == "KILL":
            # Already KILL; record + alert without re-snapshotting a
            # state change (just appends a no-op transition entry to
            # the audit log).
            self._record_event(self._state, self._state, trigger)
            return
        self._record_event(self._state, target, trigger)
        self._state = target
        self._last_trigger = trigger

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def request_recovery(
        self,
        token: str,
        conditions: RecoveryConditions,
        *,
        at: datetime,
    ) -> Result[None, str]:
        """REQ_S_KS_009: drawdown recovered + integrity restored +
        backtests stable + manual operator confirmation. All four
        gates must clear; failure returns a categorized ``Err``.
        """
        if self._state is KillSwitchState.ACTIVE:
            return Err("safety:no_recovery_needed")
        if self.cfg.require_manual_recovery and not self.verifier.verify(token):
            return Err("safety:invalid_operator_token")
        if not conditions.all_met():
            return Err("safety:recovery_conditions_unmet")
        prior = self._state
        self._state = KillSwitchState.ACTIVE
        snapshot_id = self._next_snapshot_id()
        snapshot = AuditSnapshot(
            id=snapshot_id,
            at=at,
            state_from=prior,
            state_to=KillSwitchState.ACTIVE,
            trigger_code="manual_recovery",
            trigger_message=f"recovery from {prior.value}",
            severity="RECOVERY",
        )
        self.snapshot_sink.record(snapshot)
        self._fanout_alert("RECOVERY", _alert_payload_recovery(prior, snapshot_id, at))
        if self.notification_fanout is not None:
            self.notification_fanout.dispatch(
                _ks_event_from_recovery(prior, snapshot_id, at)
            )
        return Ok(None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _target_state_for(self, trigger: KillSwitchTrigger) -> KillSwitchState:
        if trigger.severity == "KILL":
            return KillSwitchState.KILL
        # DEGRADE: from ACTIVE -> DEGRADED; otherwise unchanged.
        if self._state is KillSwitchState.ACTIVE:
            return KillSwitchState.DEGRADED
        return self._state

    def _record_event(
        self,
        prior: KillSwitchState,
        target: KillSwitchState,
        trigger: KillSwitchTrigger,
    ) -> None:
        snapshot_id = self._next_snapshot_id()
        snapshot = AuditSnapshot(
            id=snapshot_id,
            at=trigger.raised_at,
            state_from=prior,
            state_to=target,
            trigger_code=trigger.code,
            trigger_message=trigger.message,
            severity=trigger.severity,
        )
        self.snapshot_sink.record(snapshot)
        self._fanout_alert(
            trigger.severity, _alert_payload_trigger(prior, target, trigger, snapshot_id)
        )
        # CR-001 Phase B bridge — REQ_F_NOT_003 / REQ_SDD_NOT_002.
        # Existing deployments leave ``notification_fanout`` unset
        # and skip this call entirely (bit-identical to pre-bridge
        # behaviour).
        if self.notification_fanout is not None:
            self.notification_fanout.dispatch(
                _ks_event_from_trigger(prior, target, trigger, snapshot_id)
            )

    def _next_snapshot_id(self) -> SnapshotId:
        return SnapshotId(f"{self.cfg.snapshot_id_prefix}-{next(self._seq):08d}")

    def _fanout_alert(self, severity: str, payload: dict[str, Any]) -> None:
        log = logging.getLogger(__name__)
        for channel in self.alert_channels:
            result = deliver_with_retry(channel, severity, payload)
            match result:
                case Err(reason):
                    log.error("alert delivery exhausted retries: %s", reason)
                case Ok(_):
                    pass


def _alert_payload_trigger(
    prior: KillSwitchState,
    target: KillSwitchState,
    trigger: KillSwitchTrigger,
    snapshot_id: SnapshotId,
) -> dict[str, Any]:
    return {
        "snapshot_id": str(snapshot_id),
        "state_from": prior.value,
        "state_to": target.value,
        "trigger_category": trigger.category.value,
        "trigger_code": trigger.code,
        "severity": trigger.severity,
        "message": trigger.message,
        "raised_at": trigger.raised_at.isoformat(),
    }


def _ks_event_from_trigger(
    prior: KillSwitchState,
    target: KillSwitchState,
    trigger: KillSwitchTrigger,
    snapshot_id: SnapshotId,
) -> KillSwitchEvent:
    """Build a typed ``KillSwitchEvent`` mirroring the dict payload
    above. Used by the CR-001 NotificationFanOut bridge."""
    sev = _normalise_severity(trigger.severity)
    return KillSwitchEvent(
        snapshot_id=snapshot_id,
        state_from=prior,
        state_to=target,
        trigger_code=trigger.code,
        severity=sev,
        summary=trigger.message or trigger.code,
    )


def _ks_event_from_recovery(
    prior: KillSwitchState,
    snapshot_id: SnapshotId,
    at: datetime,
) -> KillSwitchEvent:
    """``KillSwitchEvent`` for the recovery transition. ``severity``
    is the documented ``RECOVERY`` literal."""
    return KillSwitchEvent(
        snapshot_id=snapshot_id,
        state_from=prior,
        state_to=KillSwitchState.ACTIVE,
        trigger_code="manual_recovery",
        severity="RECOVERY",
        summary=f"recovery from {prior.value} at {at.isoformat()}",
    )


def _normalise_severity(raw: str) -> KillSwitchSeverity:
    """The legacy ``KillSwitchTrigger.severity`` is a free-form
    ``str``; the new typed payload accepts only the documented
    Literal values. Defensive guard so a hand-constructed trigger
    with a typo doesn't silently produce an invalid event — we
    panic at the boundary instead.
    """
    if raw in ("DEGRADE", "DEGRADED"):
        # The state manager uses "DEGRADE" but some callers emit
        # "DEGRADED" (matches the state value); both map to the
        # canonical Literal.
        return "DEGRADE"
    if raw in ("KILL",):
        return "KILL"
    if raw in ("RECOVERY",):
        return "RECOVERY"
    raise ValueError(
        f"_normalise_severity: unknown severity {raw!r}; "
        "expected DEGRADE / KILL / RECOVERY"
    )


def _alert_payload_recovery(
    prior: KillSwitchState, snapshot_id: SnapshotId, at: datetime
) -> dict[str, Any]:
    return {
        "snapshot_id": str(snapshot_id),
        "state_from": prior.value,
        "state_to": KillSwitchState.ACTIVE.value,
        "trigger_category": TriggerCategory.INTEGRITY.value,
        "trigger_code": "manual_recovery",
        "severity": "RECOVERY",
        "message": f"recovered from {prior.value}",
        "at": at.isoformat(),
    }
