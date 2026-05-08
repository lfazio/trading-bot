"""Tests for ``trading_system.backtesting.market_replay``.

Covers TC_BCT_011 (tick ordering deterministic) and the bar->tick
conversion. Determinism per REQ_SDD_ALG_019.

REQ refs: REQ_F_BCT_001, REQ_NF_DET_001, REQ_SDD_ALG_019,
REQ_SDS_INT_002.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.backtesting.clock import EventClock
from trading_system.backtesting.market_replay import MarketReplay
from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err


def _stock(symbol: str, iid: str) -> Stock:
    return Stock(
        id=InstrumentId(iid),
        symbol=symbol,
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_invalid_range_returns_err() -> None:
    data = MockMarketDataProvider(seed=1)
    res = MarketReplay.try_new(
        data,
        instruments=(_stock("A", "A.AS"),),
        timeframe=Timeframe.D1,
        start=_ts(10),
        end=_ts(5),
    )
    assert isinstance(res, Err)
    assert "invalid_range" in res.error


def test_negative_spread_returns_err() -> None:
    data = MockMarketDataProvider(seed=1)
    res = MarketReplay.try_new(
        data,
        instruments=(_stock("A", "A.AS"),),
        timeframe=Timeframe.D1,
        start=_ts(1),
        end=_ts(5),
        spread_pct=Decimal("-0.01"),
    )
    assert isinstance(res, Err)
    assert "bad_spread" in res.error


# ---------------------------------------------------------------------------
# Ordering — TC_BCT_011 (REQ_SDD_ALG_019)
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_two_instruments_same_timestamp_sorted_by_id(self) -> None:
        # Sort canonical: (timestamp ASC, instrument_id ASC).
        data = MockMarketDataProvider(seed=1)
        instruments = (_stock("B", "BBB"), _stock("A", "AAA"))
        replay = MarketReplay.try_new(
            data,
            instruments=instruments,
            timeframe=Timeframe.D1,
            start=_ts(1),
            end=_ts(3),
        ).unwrap()
        clock = EventClock()
        ticks = list(replay.stream(clock))
        # Group by timestamp; within each group iids should be ascending.
        # 3 days x 2 instruments = 6 ticks.
        assert len(ticks) == 6
        # Day 1: AAA, BBB; Day 2: AAA, BBB; Day 3: AAA, BBB.
        for i in range(0, 6, 2):
            assert str(ticks[i].instrument_id) == "AAA"
            assert str(ticks[i + 1].instrument_id) == "BBB"

    def test_clock_advances_to_tick_timestamp(self) -> None:
        data = MockMarketDataProvider(seed=1)
        replay = MarketReplay.try_new(
            data,
            instruments=(_stock("A", "AAA"),),
            timeframe=Timeframe.D1,
            start=_ts(1),
            end=_ts(3),
        ).unwrap()
        clock = EventClock()
        for tick in replay.stream(clock):
            assert clock.now() == tick.at

    def test_replay_is_deterministic(self) -> None:
        data1 = MockMarketDataProvider(seed=42)
        data2 = MockMarketDataProvider(seed=42)
        instruments = (_stock("A", "AAA"), _stock("B", "BBB"))
        r1 = MarketReplay.try_new(data1, instruments, Timeframe.D1, _ts(1), _ts(5)).unwrap()
        r2 = MarketReplay.try_new(data2, instruments, Timeframe.D1, _ts(1), _ts(5)).unwrap()
        c1, c2 = EventClock(), EventClock()
        ticks1 = list(r1.stream(c1))
        ticks2 = list(r2.stream(c2))
        assert ticks1 == ticks2


# ---------------------------------------------------------------------------
# Bar -> Tick conversion
# ---------------------------------------------------------------------------


class TestBarConversion:
    def test_zero_spread_bid_ask_equal_close(self) -> None:
        data = MockMarketDataProvider(seed=1)
        replay = MarketReplay.try_new(
            data,
            instruments=(_stock("A", "AAA"),),
            timeframe=Timeframe.D1,
            start=_ts(1),
            end=_ts(1),
        ).unwrap()
        ticks = list(replay.stream(EventClock()))
        assert len(ticks) == 1
        t = ticks[0]
        assert t.bid == t.last == t.ask

    def test_spread_widens_bid_ask(self) -> None:
        data = MockMarketDataProvider(seed=1)
        replay = MarketReplay.try_new(
            data,
            instruments=(_stock("A", "AAA"),),
            timeframe=Timeframe.D1,
            start=_ts(1),
            end=_ts(1),
            spread_pct=Decimal("0.01"),
        ).unwrap()
        t = next(iter(replay.stream(EventClock())))
        assert t.bid < t.last < t.ask
        # Half-spread on each side.
        assert t.ask - t.last == pytest.approx(t.last - t.bid)


# ---------------------------------------------------------------------------
# len() / empty range
# ---------------------------------------------------------------------------


def test_empty_range_yields_zero_ticks() -> None:
    # Mock provider's bars() returns at least one bar for start==end;
    # to test "no ticks" we need a range where start > end (which
    # try_new rejects). The next-best test: zero instruments.
    data = MockMarketDataProvider(seed=1)
    replay = MarketReplay.try_new(
        data,
        instruments=(),
        timeframe=Timeframe.D1,
        start=_ts(1),
        end=_ts(5),
    ).unwrap()
    assert len(replay) == 0
    assert list(replay.stream(EventClock())) == []


def test_throughput_deterministic_path() -> None:
    # REQ_SDD_PER_004 — >=10k ticks/s on the deterministic mock.
    # We measure stream() iteration only (engine work is tested
    # elsewhere). Loose threshold to avoid flake on slow CI.
    data = MockMarketDataProvider(seed=1)
    instruments = tuple(_stock(f"S{i}", f"S{i:04d}") for i in range(10))
    # 100 days x 10 instruments = 1000 ticks; iterate 20 times => 20k ticks.
    replay = MarketReplay.try_new(
        data,
        instruments,
        Timeframe.D1,
        _ts(1),
        _ts(1) + timedelta(days=99),
    ).unwrap()
    n_ticks = 0
    t0 = time.perf_counter()
    for _ in range(20):
        clock = EventClock()
        for _t in replay.stream(clock):
            n_ticks += 1
    elapsed = time.perf_counter() - t0
    # 20_000 ticks / elapsed seconds; require >= 10k ticks/s.
    assert n_ticks == 20_000
    assert (n_ticks / elapsed) >= 10_000, (
        f"throughput {n_ticks / elapsed:.0f} ticks/s below 10k threshold"
    )
