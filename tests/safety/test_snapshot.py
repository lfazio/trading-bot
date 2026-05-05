"""Tests for ``trading_system.safety.snapshot``."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import KillSwitchState
from trading_system.safety.snapshot import (
    AuditSnapshot,
    FileSnapshotSink,
    MemorySnapshotSink,
    SnapshotSink,
)


def make_snapshot(**overrides: object) -> AuditSnapshot:
    base: dict[str, object] = {
        "id": SnapshotId("snap-1"),
        "at": datetime(2026, 5, 4, 12, 0),
        "state_from": KillSwitchState.ACTIVE,
        "state_to": KillSwitchState.KILL,
        "trigger_code": "dd_breach",
        "trigger_message": "drawdown 0.20 exceeds cap",
        "severity": "KILL",
        "payload": {"equity": Decimal("1000.50"), "cash": Decimal("500")},
    }
    base.update(overrides)
    return AuditSnapshot(**base)  # type: ignore[arg-type]


class TestAuditSnapshot:
    def test_basic(self) -> None:
        s = make_snapshot()
        assert s.severity == "KILL"

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"AuditSnapshot\.id"):
            make_snapshot(id=SnapshotId(""))

    def test_empty_trigger_code_rejected(self) -> None:
        with pytest.raises(ValueError, match="trigger_code"):
            make_snapshot(trigger_code="")

    def test_invalid_severity_rejected(self) -> None:
        with pytest.raises(ValueError, match="severity"):
            make_snapshot(severity="WARN")

    def test_recovery_severity_allowed(self) -> None:
        s = make_snapshot(severity="RECOVERY")
        assert s.severity == "RECOVERY"


class TestSnapshotSinkProtocol:
    def test_memory_sink_satisfies(self) -> None:
        assert isinstance(MemorySnapshotSink(), SnapshotSink)

    def test_file_sink_satisfies(self, tmp_path: Path) -> None:
        sink = FileSnapshotSink(path=tmp_path / "audit.jsonl")
        assert isinstance(sink, SnapshotSink)


class TestMemorySnapshotSink:
    def test_records_in_order(self) -> None:
        sink = MemorySnapshotSink()
        a = make_snapshot(id=SnapshotId("snap-1"))
        b = make_snapshot(id=SnapshotId("snap-2"), severity="DEGRADE")
        sink.record(a)
        sink.record(b)
        assert sink.snapshots == [a, b]


class TestFileSnapshotSink:
    def test_appends_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        sink = FileSnapshotSink(path=path)
        sink.record(make_snapshot())
        sink.record(
            make_snapshot(
                id=SnapshotId("snap-2"),
                severity="DEGRADE",
                state_to=KillSwitchState.DEGRADED,
            )
        )

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["id"] == "snap-1"
        assert first["severity"] == "KILL"
        assert first["state_from"] == "active"
        assert first["state_to"] == "kill"
        # Decimal payloads are preserved as strings.
        assert first["payload"]["equity"] == "1000.50"
        # ISO datetime round-trips.
        assert first["at"] == "2026-05-04T12:00:00"
