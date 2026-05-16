"""Tests for the bundled notification channels + the Protocol
Channels SHALL satisfy (REQ_F_NOT_002 / REQ_SDS_NOT_001)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import KillSwitchState
from trading_system.notifications.channel import (
    AlertChannel,
    NotificationChannel,
)
from trading_system.notifications.channels.local_log import (
    LocalLogChannel,
    MemoryNotificationChannel,
)
from trading_system.notifications.payloads import KillSwitchEvent
from trading_system.result import Err, Ok


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _ks_event() -> KillSwitchEvent:
    return KillSwitchEvent(
        snapshot_id=SnapshotId("snap-1"),
        state_from=KillSwitchState.ACTIVE,
        state_to=KillSwitchState.DEGRADED,
        trigger_code="financial:single_day_loss",
        severity="DEGRADE",
        summary="single-day loss breach",
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_local_log_channel_satisfies_notification_channel(tmp_path: Path) -> None:
    ch = LocalLogChannel(path=tmp_path / "log.jsonl")
    assert isinstance(ch, NotificationChannel)


def test_local_log_channel_satisfies_alert_channel(tmp_path: Path) -> None:
    ch = LocalLogChannel(path=tmp_path / "log.jsonl")
    assert isinstance(ch, AlertChannel)


def test_memory_channel_satisfies_both_protocols() -> None:
    ch = MemoryNotificationChannel()
    assert isinstance(ch, NotificationChannel)
    assert isinstance(ch, AlertChannel)


# ---------------------------------------------------------------------------
# LocalLogChannel write semantics
# ---------------------------------------------------------------------------


def test_local_log_writes_one_canonical_json_line(tmp_path: Path) -> None:
    log_path = tmp_path / "log.jsonl"
    ch = LocalLogChannel(path=log_path)
    result = ch.deliver(_ks_event())
    assert isinstance(result, Ok)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["snapshot_id"] == "snap-1"
    # KillSwitchState StrEnum value is lowercase ("active" /
    # "degraded"); the canonical serialiser emits ``.value``.
    assert obj["state_from"] == "active"
    assert obj["state_to"] == "degraded"
    assert obj["severity"] == "DEGRADE"


def test_local_log_appends_on_repeated_deliver(tmp_path: Path) -> None:
    log_path = tmp_path / "log.jsonl"
    ch = LocalLogChannel(path=log_path)
    ch.deliver(_ks_event()).unwrap()
    ch.deliver(_ks_event()).unwrap()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_local_log_returns_io_err_on_unwritable_path(tmp_path: Path) -> None:
    # Point to a directory rather than a file → open(..., "a") fails.
    ch = LocalLogChannel(path=tmp_path)  # directory, not file
    match ch.deliver(_ks_event()):
        case Err(reason):
            assert reason.startswith("notifications:io:")
        case _:
            raise AssertionError("expected Err")


def test_local_log_resolves_string_path(tmp_path: Path) -> None:
    """``path`` is normalised to ``Path`` by ``__post_init__``."""
    log_path = tmp_path / "log.jsonl"
    ch = LocalLogChannel(path=str(log_path))  # type: ignore[arg-type]
    ch.deliver(_ks_event()).unwrap()
    assert log_path.exists()


# ---------------------------------------------------------------------------
# MemoryNotificationChannel
# ---------------------------------------------------------------------------


def test_memory_channel_records_payloads() -> None:
    ch = MemoryNotificationChannel()
    e = _ks_event()
    assert isinstance(ch.deliver(e), Ok)
    assert ch.delivered == [e]


def test_memory_channel_starts_empty() -> None:
    ch = MemoryNotificationChannel()
    assert ch.delivered == []
