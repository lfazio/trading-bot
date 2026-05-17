"""Tests for the CR-001 Phase B safety ⇒ NotificationFanOut bridge
(REQ_F_NOT_003 / REQ_SDD_NOT_002).

When ``StateManager.notification_fanout`` is wired, KS state
transitions (and recovery) emit a typed ``KillSwitchEvent`` through
the new fan-out *in addition to* the legacy AlertChannel path. When
the field is left unset (default), the legacy path is unchanged
— REQ_F_NOT_003 backwards compatibility.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.notifications.channels.local_log import (
    MemoryNotificationChannel,
)
from trading_system.notifications.fanout import (
    NotificationFanOut,
    RetryPolicy,
)
from trading_system.notifications.payloads import KillSwitchEvent
from trading_system.result import Ok
from trading_system.safety.alerts import MemoryAlertChannel
from trading_system.safety.recovery import (
    OperatorTokenVerifier,
    RecoveryConditions,
)
from trading_system.safety.snapshot import MemorySnapshotSink
from trading_system.safety.state_manager import (
    StateManager,
    _normalise_severity,
)


_NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


class _StaticVerifier:
    """Trivial verifier that accepts a fixed token — recovery tests
    only need an accept/deny binary."""

    def __init__(self, *, accept: bool) -> None:
        self._accept = accept

    def verify(self, token: str) -> bool:
        return self._accept


def _trigger(
    *,
    severity: str = "DEGRADE",
    code: str = "financial:single_day_loss",
    category: TriggerCategory = TriggerCategory.FINANCIAL,
    message: str = "single-day loss breach",
) -> KillSwitchTrigger:
    return KillSwitchTrigger(
        category=category,
        code=code,
        message=message,
        severity=severity,  # type: ignore[arg-type]
        raised_at=_NOW,
        snapshot_id=SnapshotId("trigger-snap-1"),
    )


def _state_manager(
    *,
    fanout: NotificationFanOut | None = None,
    verifier_accept: bool = True,
) -> tuple[StateManager, MemoryNotificationChannel | None, MemoryAlertChannel]:
    legacy_channel = MemoryAlertChannel()
    sm = StateManager(
        verifier=_StaticVerifier(accept=verifier_accept),
        snapshot_sink=MemorySnapshotSink(),
        alert_channels=[legacy_channel],
        notification_fanout=fanout,
    )
    mem_ch = None
    if fanout is not None and fanout.channels:
        first = fanout.channels[0]
        if isinstance(first, MemoryNotificationChannel):
            mem_ch = first
    return sm, mem_ch, legacy_channel


def _fanout_with_memory() -> tuple[NotificationFanOut, MemoryNotificationChannel]:
    ch = MemoryNotificationChannel()
    fan = NotificationFanOut(
        channels=(ch,),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
        sleep=lambda _t: None,
    )
    return fan, ch


# ---------------------------------------------------------------------------
# Backwards compat — no fanout configured (REQ_F_NOT_003)
# ---------------------------------------------------------------------------


def test_no_fanout_leaves_legacy_path_unchanged() -> None:
    sm, mem_ch, legacy = _state_manager(fanout=None)
    sm.raise_trigger(_trigger(severity="DEGRADE"))
    assert sm.state() is KillSwitchState.DEGRADED
    # Legacy AlertChannel still fires.
    assert len(legacy.delivered) == 1
    assert mem_ch is None  # no fanout configured


# ---------------------------------------------------------------------------
# Trigger paths — DEGRADE / KILL / same-state idempotent
# ---------------------------------------------------------------------------


def test_degrade_dispatches_typed_event() -> None:
    fanout, ch = _fanout_with_memory()
    sm, _mem, legacy = _state_manager(fanout=fanout)
    sm.raise_trigger(_trigger(severity="DEGRADE"))

    # Both paths fired.
    assert len(legacy.delivered) == 1
    assert len(ch.delivered) == 1

    event = ch.delivered[0]
    assert isinstance(event, KillSwitchEvent)
    assert event.severity == "DEGRADE"
    assert event.state_from is KillSwitchState.ACTIVE
    assert event.state_to is KillSwitchState.DEGRADED
    assert event.trigger_code == "financial:single_day_loss"
    assert event.summary == "single-day loss breach"


def test_kill_dispatches_typed_event() -> None:
    fanout, ch = _fanout_with_memory()
    sm, _mem, _legacy = _state_manager(fanout=fanout)
    sm.raise_trigger(_trigger(severity="KILL"))

    assert sm.state() is KillSwitchState.KILL
    event = ch.delivered[0]
    assert event.severity == "KILL"
    assert event.state_to is KillSwitchState.KILL


def test_same_state_degrade_still_emits_audit_event() -> None:
    """REQ_S_KS_007 family — repeated same-state DEGRADE triggers
    record an audit row + emit a notification so the operator sees
    every fire, even if the state machine doesn't progress."""
    fanout, ch = _fanout_with_memory()
    sm, _mem, _legacy = _state_manager(fanout=fanout)
    sm.raise_trigger(_trigger(severity="DEGRADE"))
    sm.raise_trigger(_trigger(severity="DEGRADE"))
    assert len(ch.delivered) == 2
    # Second event: same state on both sides.
    assert ch.delivered[1].state_from is KillSwitchState.DEGRADED
    assert ch.delivered[1].state_to is KillSwitchState.DEGRADED


def test_same_state_kill_emits_event() -> None:
    fanout, ch = _fanout_with_memory()
    sm, _mem, _legacy = _state_manager(fanout=fanout)
    sm.raise_trigger(_trigger(severity="KILL"))
    sm.raise_trigger(_trigger(severity="KILL"))
    assert len(ch.delivered) == 2
    assert ch.delivered[1].state_to is KillSwitchState.KILL


# ---------------------------------------------------------------------------
# Recovery path
# ---------------------------------------------------------------------------


def test_recovery_dispatches_typed_event() -> None:
    fanout, ch = _fanout_with_memory()
    sm, _mem, legacy = _state_manager(fanout=fanout, verifier_accept=True)
    sm.raise_trigger(_trigger(severity="DEGRADE"))
    # Recovery requires all-met RecoveryConditions; build one accordingly.
    conditions = RecoveryConditions(
        drawdown_recovered=True,
        integrity_restored=True,
        backtests_stable=True,
    )
    result = sm.request_recovery("any-token", conditions, at=_NOW)
    assert isinstance(result, Ok)

    # The fan-out saw: 1× DEGRADE trigger + 1× RECOVERY event.
    assert len(ch.delivered) == 2
    recovery_event = ch.delivered[1]
    assert recovery_event.severity == "RECOVERY"
    assert recovery_event.state_to is KillSwitchState.ACTIVE
    assert recovery_event.trigger_code == "manual_recovery"
    # Legacy path also fired both times.
    assert len(legacy.delivered) == 2


def test_recovery_with_no_fanout_unchanged() -> None:
    """REQ_F_NOT_003 — legacy deployments without a fan-out keep
    working: recovery still flows through the AlertChannel path."""
    sm, _mem, legacy = _state_manager(fanout=None)
    sm.raise_trigger(_trigger(severity="DEGRADE"))
    conditions = RecoveryConditions(
        drawdown_recovered=True,
        integrity_restored=True,
        backtests_stable=True,
    )
    result = sm.request_recovery("any-token", conditions, at=_NOW)
    assert isinstance(result, Ok)
    # Legacy path: 1× DEGRADE + 1× RECOVERY.
    assert len(legacy.delivered) == 2


# ---------------------------------------------------------------------------
# severity normalisation
# ---------------------------------------------------------------------------


def test_normalise_severity_accepts_degrade_aliases() -> None:
    assert _normalise_severity("DEGRADE") == "DEGRADE"
    assert _normalise_severity("DEGRADED") == "DEGRADE"


def test_normalise_severity_accepts_documented_literals() -> None:
    assert _normalise_severity("KILL") == "KILL"
    assert _normalise_severity("RECOVERY") == "RECOVERY"


def test_normalise_severity_rejects_unknown_value() -> None:
    import pytest

    with pytest.raises(ValueError, match="severity"):
        _normalise_severity("WARN")


# ---------------------------------------------------------------------------
# Replay determinism — REQ_NF_NOT_002 family
# ---------------------------------------------------------------------------


def test_two_runs_produce_equal_events() -> None:
    """Identical inputs through the bridge SHALL produce equal
    KillSwitchEvent payloads (REQ_NF_NOT_002 family)."""
    fanout_a, ch_a = _fanout_with_memory()
    fanout_b, ch_b = _fanout_with_memory()
    sm_a, _ma, _la = _state_manager(fanout=fanout_a)
    sm_b, _mb, _lb = _state_manager(fanout=fanout_b)
    t = _trigger(severity="DEGRADE")
    sm_a.raise_trigger(t)
    sm_b.raise_trigger(t)
    # snapshot_id differs (the SnapshotId sequence is per-instance),
    # but the rest of the event SHALL match.
    a = ch_a.delivered[0]
    b = ch_b.delivered[0]
    assert isinstance(a, KillSwitchEvent)
    assert isinstance(b, KillSwitchEvent)
    assert a.state_from == b.state_from
    assert a.state_to == b.state_to
    assert a.trigger_code == b.trigger_code
    assert a.severity == b.severity
    assert a.summary == b.summary


# ---------------------------------------------------------------------------
# Channel-failure isolation — REQ_NF_NOT_001
# ---------------------------------------------------------------------------


def test_fanout_failure_does_not_break_state_machine() -> None:
    """A channel that fails permanently SHALL NOT propagate up
    through the state manager — the trade-execution critical path
    is unaffected (REQ_NF_NOT_001)."""
    from dataclasses import dataclass, field
    from trading_system.result import Err

    @dataclass(slots=True)
    class _AlwaysFail:
        delivered: list[object] = field(default_factory=list)

        def deliver(self, payload):
            return Err("network:permanent")

    bad = _AlwaysFail()
    fan = NotificationFanOut(
        channels=(bad,),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
        sleep=lambda _t: None,
    )
    sm, _mem, _legacy = _state_manager(fanout=fan)
    # Should NOT raise.
    sm.raise_trigger(_trigger(severity="KILL"))
    assert sm.state() is KillSwitchState.KILL
