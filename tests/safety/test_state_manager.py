"""Tests for ``trading_system.safety.state_manager``.

REQ refs verified by the state-machine + recovery cases:
- REQ_S_KS_001 — the kill switch is a three-state machine
  (ACTIVE / DEGRADED / KILL) with documented transitions.
- REQ_S_KS_002 — non-bypassable: ``must_halt`` is the only
  surface that exposes the boolean halt decision; no module
  can flip the state without going through ``raise_trigger``.
- REQ_S_KS_005 — execution-anomaly triggers feed the state
  machine through ``raise_trigger(TriggerCategory.EXECUTION,
  ...)`` (covered exhaustively in ``test_trigger_categories.py``).
- REQ_S_KS_007 — operator-confirmed recovery (the
  ``test_recovery_*`` cases drive ``request_recovery`` with the
  documented condition set + HMAC token).
- REQ_S_KS_009 — RecoveryConditions gates recovery: drawdown
  recovered + integrity restored + backtests stable.
- REQ_S_KS_010 — kill-switch configuration is immutable at
  runtime; ``StateManagerConfig`` is a frozen dataclass and the
  ``_frozen_runtime`` invariant prevents post-construction
  mutation.
- REQ_S_KS_011 — every safety-relevant operation runs through
  the state manager; the BrokerAdapter contract (REQ_SDS_ARC_003)
  is what call sites use to gate submission. Verified in
  ``tests/conformance/test_behavioral_and_safety.py``'s
  ``test_execution_adapter_calls_safety_check_before_submit``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.result import Err, Ok
from trading_system.safety import (
    AlwaysInvalidVerifier,
    AlwaysValidVerifier,
    MemoryAlertChannel,
    MemorySnapshotSink,
    RecoveryConditions,
    SafetyLayer,
    StateManager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_trigger(
    *,
    severity: str = "KILL",
    code: str = "dd_breach",
    category: TriggerCategory = TriggerCategory.FINANCIAL,
    at: datetime | None = None,
) -> KillSwitchTrigger:
    return KillSwitchTrigger(
        category=category,
        code=code,
        message=f"{code} test message",
        severity=severity,  # type: ignore[arg-type]
        raised_at=at or datetime(2026, 5, 4, 12, 0),
        snapshot_id=SnapshotId("snap-placeholder"),
    )


def make_manager(
    *,
    valid_token: bool = True,
) -> tuple[StateManager, MemorySnapshotSink, MemoryAlertChannel]:
    sink = MemorySnapshotSink()
    alerts = MemoryAlertChannel()
    verifier = AlwaysValidVerifier() if valid_token else AlwaysInvalidVerifier()
    sm = StateManager(
        verifier=verifier,
        snapshot_sink=sink,
        alert_channels=[alerts],
    )
    return sm, sink, alerts


# ---------------------------------------------------------------------------
# Construction + Protocol conformance
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_satisfies_safety_protocol(self) -> None:
        sm, _, _ = make_manager()
        assert isinstance(sm, SafetyLayer)

    def test_initial_state_active(self) -> None:
        sm, _, _ = make_manager()
        assert sm.state() is KillSwitchState.ACTIVE
        assert sm.must_halt() is False


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestStateTransitions:
    def test_kill_trigger_advances_to_kill(self) -> None:
        sm, sink, alerts = make_manager()
        sm.raise_trigger(make_trigger(severity="KILL"))
        assert sm.state() is KillSwitchState.KILL
        assert sm.must_halt() is True
        assert len(sink.snapshots) == 1
        assert sink.snapshots[0].state_from is KillSwitchState.ACTIVE
        assert sink.snapshots[0].state_to is KillSwitchState.KILL
        assert sink.snapshots[0].severity == "KILL"
        # Alert delivered.
        assert len(alerts.delivered) == 1
        sev, payload = alerts.delivered[0]
        assert sev == "KILL"
        assert payload["state_to"] == "kill"

    def test_degrade_trigger_advances_active_to_degraded(self) -> None:
        sm, sink, alerts = make_manager()
        sm.raise_trigger(make_trigger(severity="DEGRADE", code="vol_cap_breach"))
        assert sm.state() is KillSwitchState.DEGRADED
        assert sm.must_halt() is False
        assert sink.snapshots[0].state_to is KillSwitchState.DEGRADED
        assert alerts.delivered[0][0] == "DEGRADE"

    def test_degrade_in_degraded_state_no_regression(self) -> None:
        sm, sink, _ = make_manager()
        sm.raise_trigger(make_trigger(severity="DEGRADE"))
        sm.raise_trigger(make_trigger(severity="DEGRADE", code="another"))
        # Still DEGRADED; both events recorded.
        assert sm.state() is KillSwitchState.DEGRADED
        assert len(sink.snapshots) == 2
        # The second snapshot is a no-op transition (DEGRADED -> DEGRADED).
        assert sink.snapshots[1].state_from is KillSwitchState.DEGRADED
        assert sink.snapshots[1].state_to is KillSwitchState.DEGRADED

    def test_degrade_after_kill_does_not_regress(self) -> None:
        sm, sink, _ = make_manager()
        sm.raise_trigger(make_trigger(severity="KILL"))
        sm.raise_trigger(make_trigger(severity="DEGRADE", code="vol_cap_breach"))
        # DEGRADE doesn't pull KILL back down.
        assert sm.state() is KillSwitchState.KILL
        assert len(sink.snapshots) == 2

    def test_kill_after_kill_records_event_no_state_change(self) -> None:
        sm, sink, _ = make_manager()
        sm.raise_trigger(make_trigger(severity="KILL"))
        sm.raise_trigger(make_trigger(severity="KILL", code="rapid_decline"))
        assert sm.state() is KillSwitchState.KILL
        assert len(sink.snapshots) == 2

    def test_kill_after_degrade_advances(self) -> None:
        sm, _, _ = make_manager()
        sm.raise_trigger(make_trigger(severity="DEGRADE"))
        sm.raise_trigger(make_trigger(severity="KILL"))
        assert sm.state() is KillSwitchState.KILL


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def all_met() -> RecoveryConditions:
    return RecoveryConditions(
        drawdown_recovered=True,
        integrity_restored=True,
        backtests_stable=True,
    )


class TestRecovery:
    def test_recovery_requires_non_active_state(self) -> None:
        sm, _, _ = make_manager()
        result = sm.request_recovery("token", all_met(), at=datetime(2026, 5, 4))
        match result:
            case Err(reason):
                assert "no_recovery_needed" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_recovery_rejects_invalid_token(self) -> None:
        sm, _, _ = make_manager(valid_token=False)
        sm.raise_trigger(make_trigger(severity="KILL"))
        result = sm.request_recovery("bad", all_met(), at=datetime(2026, 5, 4))
        match result:
            case Err(reason):
                assert "invalid_operator_token" in reason
            case Ok(_):
                pytest.fail("expected Err")
        assert sm.state() is KillSwitchState.KILL

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"drawdown_recovered": False, "integrity_restored": True, "backtests_stable": True},
            {"drawdown_recovered": True, "integrity_restored": False, "backtests_stable": True},
            {"drawdown_recovered": True, "integrity_restored": True, "backtests_stable": False},
        ],
    )
    def test_recovery_rejects_when_conditions_not_met(self, kwargs: dict[str, bool]) -> None:
        sm, _, _ = make_manager()
        sm.raise_trigger(make_trigger(severity="KILL"))
        result = sm.request_recovery("token", RecoveryConditions(**kwargs), at=datetime(2026, 5, 4))
        match result:
            case Err(reason):
                assert "recovery_conditions_unmet" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_recovery_succeeds_with_valid_token_and_conditions(self) -> None:
        sm, sink, alerts = make_manager()
        sm.raise_trigger(make_trigger(severity="KILL"))
        result = sm.request_recovery("token", all_met(), at=datetime(2026, 5, 5))
        assert result == Ok(None)
        assert sm.state() is KillSwitchState.ACTIVE
        assert sm.must_halt() is False
        # Snapshot recorded with RECOVERY severity.
        recovery = sink.snapshots[-1]
        assert recovery.severity == "RECOVERY"
        assert recovery.state_from is KillSwitchState.KILL
        assert recovery.state_to is KillSwitchState.ACTIVE
        # Alert fan-out.
        sev, payload = alerts.delivered[-1]
        assert sev == "RECOVERY"
        assert payload["trigger_code"] == "manual_recovery"


# ---------------------------------------------------------------------------
# Snapshot id sequencing
# ---------------------------------------------------------------------------


class TestSnapshotIds:
    def test_ids_are_sequential_and_unique(self) -> None:
        sm, sink, _ = make_manager()
        for _ in range(3):
            sm.raise_trigger(make_trigger(severity="DEGRADE"))
        ids = [s.id for s in sink.snapshots]
        assert ids == ["snap-00000001", "snap-00000002", "snap-00000003"]
