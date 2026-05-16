"""Tests for ``canonical_json_line`` determinism (REQ_NF_NOT_002)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import KillSwitchState
from trading_system.notifications.canonical import canonical_json_line
from trading_system.notifications.payloads import (
    KillSwitchEvent,
)


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


def test_two_calls_with_same_input_produce_identical_strings() -> None:
    a = canonical_json_line(_ks_event())
    b = canonical_json_line(_ks_event())
    assert a == b


def test_output_is_single_line() -> None:
    line = canonical_json_line(_ks_event())
    assert "\n" not in line


def test_decimal_serialises_as_string() -> None:
    line = canonical_json_line({"amount": Decimal("123.456")})
    assert json.loads(line)["amount"] == "123.456"


def test_decimal_preserves_precision() -> None:
    """REQ_F_PER_005 family — Decimal-as-TEXT, no float intermediates."""
    big = Decimal("99999999999999999999999.99999999")
    line = canonical_json_line({"v": big})
    # str(Decimal) preserves the full repr.
    assert json.loads(line)["v"] == "99999999999999999999999.99999999"


def test_datetime_serialises_as_iso() -> None:
    line = canonical_json_line({"at": _NOW})
    assert json.loads(line)["at"] == "2026-05-16T12:00:00+00:00"


def test_enum_serialises_as_value() -> None:
    class Severity(StrEnum):
        DEGRADE = "DEGRADE"
        KILL = "KILL"

    line = canonical_json_line({"sev": Severity.DEGRADE})
    assert json.loads(line)["sev"] == "DEGRADE"


def test_dataclass_serialises_as_field_dict() -> None:
    @dataclass(frozen=True, slots=True)
    class Row:
        a: int
        b: str

    line = canonical_json_line(Row(a=1, b="x"))
    obj = json.loads(line)
    assert obj == {"a": 1, "b": "x"}


def test_mapping_keys_sorted_for_determinism() -> None:
    a = canonical_json_line({"z": 1, "a": 2, "m": 3})
    b = canonical_json_line({"a": 2, "m": 3, "z": 1})
    assert a == b
    obj = json.loads(a)
    assert list(obj.keys()) == ["a", "m", "z"]


def test_nested_decimal_in_list() -> None:
    line = canonical_json_line({"prices": [Decimal("1.00"), Decimal("2.50")]})
    assert json.loads(line)["prices"] == ["1.00", "2.50"]


def test_nested_dataclass_in_mapping() -> None:
    @dataclass(frozen=True, slots=True)
    class Row:
        v: Decimal

    line = canonical_json_line({"row": Row(v=Decimal("0.30"))})
    assert json.loads(line) == {"row": {"v": "0.30"}}


def test_ks_event_canonical_form_byte_identical() -> None:
    """Snapshot regression: lock the byte-form of a known KS event.

    ``KillSwitchState`` is a StrEnum whose values are lowercase
    (``"active"`` / ``"degraded"`` / etc.) — the canonical
    serialiser emits ``.value`` so the JSON form is lowercase too.
    """
    expected = (
        '{"severity":"DEGRADE",'
        '"snapshot_id":"snap-1",'
        '"state_from":"active",'
        '"state_to":"degraded",'
        '"summary":"single-day loss breach",'
        '"trigger_code":"financial:single_day_loss"}'
    )
    assert canonical_json_line(_ks_event()) == expected


def test_primitive_passthrough() -> None:
    assert canonical_json_line(None) == "null"
    assert canonical_json_line(True) == "true"
    assert canonical_json_line(42) == "42"
    assert canonical_json_line("x") == '"x"'
