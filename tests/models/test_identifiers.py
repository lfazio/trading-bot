"""Tests for ``trading_system.models.identifiers``.

NewType is a static-only construct (mypy --strict catches misuse;
runtime is just ``str``). The runtime tests confirm that values pass
through unchanged.
"""

from __future__ import annotations

from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    SnapshotId,
    StrategyId,
    TradeId,
)


def test_order_id_roundtrip() -> None:
    o = OrderId("o-123")
    assert o == "o-123"


def test_distinct_runtime_values() -> None:
    # All NewTypes alias `str`; equality is value-based at runtime.
    assert OrderId("x") == TradeId("x")
    assert OrderId("a") != OrderId("b")


def test_construction_smoke() -> None:
    assert InstrumentId("AAPL") == "AAPL"
    assert StrategyId("core_v1") == "core_v1"
    assert SnapshotId("snap-001") == "snap-001"
