"""Tests for ``trading_system.backtesting.clock``.

REQ refs: REQ_SDS_ARC_006, REQ_F_BCT_001 / REQ_NF_DET_001.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_system.backtesting.clock import EventClock


def test_now_panics_before_set() -> None:
    clk = EventClock()
    with pytest.raises(AssertionError, match=r"EventClock\.now"):
        clk.now()


def test_set_then_now_returns_value() -> None:
    clk = EventClock()
    t = datetime(2026, 5, 8, tzinfo=UTC)
    clk.set(t)
    assert clk.now() == t


def test_set_overwrites_previous() -> None:
    clk = EventClock()
    t1 = datetime(2026, 5, 8, tzinfo=UTC)
    t2 = datetime(2026, 5, 9, tzinfo=UTC)
    clk.set(t1)
    clk.set(t2)
    assert clk.now() == t2
