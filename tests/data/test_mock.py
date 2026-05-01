"""Tests for ``trading_system.data.mock``.

Covers the determinism contract (REQ_SDD_TST_002), ascending-order
guarantee (REQ_SDD_API_007), Result-typed errors (REQ_SDD_ERR_002),
and Protocol conformance (REQ_SDD_API_002).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar, Fundamentals, Timeframe
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import Instrument, InstrumentClass
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Dividend
from trading_system.result import Err, Ok, Result

EUR = Currency.EUR


def stock(symbol: str = "ABC") -> Instrument:
    return Instrument(
        id=InstrumentId(f"id-{symbol}"),
        symbol=symbol,
        exchange="EPA",
        currency=EUR,
        cls=InstrumentClass.STOCK,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_isinstance_protocol(self) -> None:
        # REQ_SDD_API_002: runtime-checkable Protocol.
        provider = MockMarketDataProvider(seed=0)
        assert isinstance(provider, MarketDataProvider)


# ---------------------------------------------------------------------------
# bars
# ---------------------------------------------------------------------------


def _unwrap_bars(result: Result[list[Bar], str]) -> list[Bar]:
    match result:
        case Ok(bars):
            return bars
        case Err(reason):
            pytest.fail(f"unexpected Err: {reason}")


class TestBars:
    def test_returns_ok_for_valid_range(self) -> None:
        provider = MockMarketDataProvider(seed=0xCAFE)
        result = provider.bars(
            stock(),
            Timeframe.D1,
            datetime(2026, 1, 1),
            datetime(2026, 1, 5),
        )
        bars = _unwrap_bars(result)
        assert len(bars) == 5

    def test_invalid_range_returns_err(self) -> None:
        provider = MockMarketDataProvider(seed=0)
        result = provider.bars(
            stock(),
            Timeframe.D1,
            datetime(2026, 1, 5),
            datetime(2026, 1, 1),
        )
        match result:
            case Err(reason):
                assert reason.startswith("data:invalid_range:")
            case Ok(_):
                pytest.fail("expected Err")

    def test_strictly_ascending_at(self) -> None:
        # REQ_SDD_API_007: bars in strictly ascending order.
        provider = MockMarketDataProvider(seed=42)
        result = provider.bars(
            stock(),
            Timeframe.H1,
            datetime(2026, 5, 1, 9, 0),
            datetime(2026, 5, 1, 18, 0),
        )
        bars = _unwrap_bars(result)
        for i in range(1, len(bars)):
            assert bars[i].at > bars[i - 1].at

    def test_deterministic_same_seed(self) -> None:
        # REQ_SDD_TST_002: identical (seed, instrument, tf, start) produces
        # identical bar series.
        a = MockMarketDataProvider(seed=42)
        b = MockMarketDataProvider(seed=42)
        ra = a.bars(stock("X"), Timeframe.D1, datetime(2026, 1, 1), datetime(2026, 1, 30))
        rb = b.bars(stock("X"), Timeframe.D1, datetime(2026, 1, 1), datetime(2026, 1, 30))
        assert _unwrap_bars(ra) == _unwrap_bars(rb)

    def test_different_seed_different_bars(self) -> None:
        a = MockMarketDataProvider(seed=1)
        b = MockMarketDataProvider(seed=2)
        ra = a.bars(stock(), Timeframe.D1, datetime(2026, 1, 1), datetime(2026, 1, 30))
        rb = b.bars(stock(), Timeframe.D1, datetime(2026, 1, 1), datetime(2026, 1, 30))
        assert _unwrap_bars(ra) != _unwrap_bars(rb)

    def test_different_instrument_different_bars(self) -> None:
        provider = MockMarketDataProvider(seed=42)
        ra = provider.bars(stock("AAA"), Timeframe.D1, datetime(2026, 1, 1), datetime(2026, 1, 30))
        rb = provider.bars(stock("BBB"), Timeframe.D1, datetime(2026, 1, 1), datetime(2026, 1, 30))
        assert _unwrap_bars(ra) != _unwrap_bars(rb)

    def test_empty_range_returns_single_bar(self) -> None:
        # start == end yields one bar at start.
        provider = MockMarketDataProvider(seed=0)
        result = provider.bars(
            stock(),
            Timeframe.D1,
            datetime(2026, 5, 1),
            datetime(2026, 5, 1),
        )
        bars = _unwrap_bars(result)
        assert len(bars) == 1
        assert bars[0].at == datetime(2026, 5, 1)

    def test_timeframe_step(self) -> None:
        provider = MockMarketDataProvider(seed=0)
        result = provider.bars(
            stock(),
            Timeframe.M5,
            datetime(2026, 5, 1, 9, 0),
            datetime(2026, 5, 1, 9, 30),
        )
        bars = _unwrap_bars(result)
        assert len(bars) == 7  # 9:00, 9:05, ..., 9:30
        assert bars[1].at - bars[0].at == timedelta(minutes=5)

    def test_bar_invariants_hold(self) -> None:
        # Every generated bar satisfies the OHLCV invariants
        # (high >= max(open, close), low <= min, prices > 0).
        provider = MockMarketDataProvider(seed=99)
        bars = _unwrap_bars(
            provider.bars(stock(), Timeframe.D1, datetime(2026, 1, 1), datetime(2026, 12, 31))
        )
        assert len(bars) > 100
        for b in bars:
            assert b.high >= max(b.open, b.close)
            assert b.low <= min(b.open, b.close)
            assert b.open > 0 and b.close > 0


# ---------------------------------------------------------------------------
# latest
# ---------------------------------------------------------------------------


class TestLatest:
    def test_requires_set_now(self) -> None:
        provider = MockMarketDataProvider(seed=0)
        result = provider.latest(stock())
        match result:
            case Err(reason):
                assert reason.startswith("data:no_as_of:")
            case Ok(_):
                pytest.fail("expected Err")

    def test_returns_bar_at_as_of(self) -> None:
        provider = MockMarketDataProvider(seed=42)
        provider.set_now(datetime(2026, 5, 1))
        result = provider.latest(stock())
        match result:
            case Ok(bar):
                assert bar.at == datetime(2026, 5, 1)
            case Err(reason):
                pytest.fail(f"unexpected Err: {reason}")

    def test_deterministic(self) -> None:
        a = MockMarketDataProvider(seed=42)
        a.set_now(datetime(2026, 5, 1))
        b = MockMarketDataProvider(seed=42)
        b.set_now(datetime(2026, 5, 1))
        assert a.latest(stock()) == b.latest(stock())


# ---------------------------------------------------------------------------
# fundamentals
# ---------------------------------------------------------------------------


class TestFundamentals:
    def _fundamentals(self) -> Fundamentals:
        return Fundamentals(
            yield_=Decimal("0.045"),
            payout_ratio=Decimal("0.55"),
            free_cash_flow=Money(Decimal(1_000_000), EUR),
            debt_equity=Decimal("0.7"),
            dividend_history_years=10,
        )

    def test_unregistered_returns_err(self) -> None:
        provider = MockMarketDataProvider(seed=0)
        result = provider.fundamentals(stock("ABC"))
        match result:
            case Err(reason):
                assert reason.startswith("data:not_found:")
            case Ok(_):
                pytest.fail("expected Err")

    def test_registered_returns_ok(self) -> None:
        provider = MockMarketDataProvider(seed=0)
        s = stock("ABC")
        f = self._fundamentals()
        provider.register_fundamentals(s.id, f)
        assert provider.fundamentals(s) == Ok(f)


# ---------------------------------------------------------------------------
# dividends
# ---------------------------------------------------------------------------


class TestDividends:
    def _dividend(self, year: int = 2026, day: int = 15) -> Dividend:
        return Dividend(
            instrument=InstrumentId("id-ABC"),
            ex_date=datetime(year, 5, day),
            pay_date=datetime(year, 6, day),
            amount_gross=Money(Decimal("2.50"), EUR),
        )

    def test_unregistered_returns_empty_list(self) -> None:
        provider = MockMarketDataProvider(seed=0)
        result = provider.dividends(stock("ABC"), 2026)
        assert result == Ok([])

    def test_registered_returns_list(self) -> None:
        provider = MockMarketDataProvider(seed=0)
        d = self._dividend()
        provider.register_dividend(d)
        result = provider.dividends(stock("ABC"), 2026)
        match result:
            case Ok(divs):
                assert divs == [d]
            case Err(reason):
                pytest.fail(f"unexpected Err: {reason}")

    def test_sorted_by_ex_date(self) -> None:
        provider = MockMarketDataProvider(seed=0)
        d_late = self._dividend(day=20)
        d_early = self._dividend(day=10)
        provider.register_dividend(d_late)
        provider.register_dividend(d_early)
        result = provider.dividends(stock("ABC"), 2026)
        match result:
            case Ok(divs):
                assert [d.ex_date for d in divs] == [d_early.ex_date, d_late.ex_date]
            case Err(reason):
                pytest.fail(f"unexpected Err: {reason}")

    def test_year_isolation(self) -> None:
        provider = MockMarketDataProvider(seed=0)
        provider.register_dividend(self._dividend(year=2025))
        result = provider.dividends(stock("ABC"), 2026)
        assert result == Ok([])
