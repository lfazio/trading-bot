"""Tests for ``trading_system.models.safety``."""

from __future__ import annotations

from datetime import datetime

import pytest

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)


def trigger(**overrides: object) -> KillSwitchTrigger:
    base: dict[str, object] = {
        "category": TriggerCategory.FINANCIAL,
        "code": "dd_breach",
        "message": "drawdown exceeded 15%",
        "severity": "KILL",
        "raised_at": datetime(2026, 5, 1, 10, 0, 0),
        "snapshot_id": SnapshotId("snap-001"),
    }
    base.update(overrides)
    return KillSwitchTrigger(**base)  # type: ignore[arg-type]


class TestKillSwitchState:
    def test_three_states(self) -> None:
        assert {s.value for s in KillSwitchState} == {"active", "degraded", "kill"}


class TestTriggerCategory:
    def test_four_categories(self) -> None:
        assert {c.value for c in TriggerCategory} == {
            "financial",
            "strategy",
            "execution",
            "integrity",
        }


class TestKillSwitchTrigger:
    def test_valid(self) -> None:
        t = trigger()
        assert t.category is TriggerCategory.FINANCIAL
        assert t.severity == "KILL"

    def test_empty_code_rejected(self) -> None:
        with pytest.raises(ValueError, match="code must be non-empty"):
            trigger(code="")

    def test_empty_message_rejected(self) -> None:
        with pytest.raises(ValueError, match="message must be non-empty"):
            trigger(message="")

    def test_empty_snapshot_rejected(self) -> None:
        with pytest.raises(ValueError, match="snapshot_id must be non-empty"):
            trigger(snapshot_id=SnapshotId(""))

    def test_invalid_severity_rejected(self) -> None:
        with pytest.raises(ValueError, match="severity must be DEGRADE or KILL"):
            trigger(severity="WARN")  # type: ignore[arg-type]
