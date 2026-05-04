"""Tests for ``trading_system.safety.protocol``."""

from __future__ import annotations

from datetime import datetime

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.safety import SafetyLayer


class StubSafety:
    """Minimal SafetyLayer test double."""

    def __init__(self) -> None:
        self._state = KillSwitchState.ACTIVE
        self.triggers: list[KillSwitchTrigger] = []

    def must_halt(self) -> bool:
        return self._state is KillSwitchState.KILL

    def state(self) -> KillSwitchState:
        return self._state

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        self.triggers.append(trigger)


class TestSafetyProtocol:
    def test_stub_satisfies_protocol(self) -> None:
        # REQ_SDD_API_002: runtime-checkable Protocol.
        assert isinstance(StubSafety(), SafetyLayer)

    def test_must_halt_default_false(self) -> None:
        s = StubSafety()
        assert s.must_halt() is False

    def test_state_default_active(self) -> None:
        s = StubSafety()
        assert s.state() is KillSwitchState.ACTIVE

    def test_raise_trigger_records(self) -> None:
        s = StubSafety()
        t = KillSwitchTrigger(
            category=TriggerCategory.FINANCIAL,
            code="dd_breach",
            message="test",
            severity="KILL",
            raised_at=datetime(2026, 5, 1),
            snapshot_id=SnapshotId("snap-1"),
        )
        s.raise_trigger(t)
        assert s.triggers == [t]
