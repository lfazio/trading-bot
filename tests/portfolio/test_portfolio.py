"""Tests for ``trading_system.portfolio.portfolio``.

REQ refs:
- REQ_F_PRT_001 — equity_after_tax is the canonical performance
  reference (cash + marked + realized_after_tax + dividends_after_tax).
- REQ_F_PRT_003 — exposure_pct per AllocationBucket.
- REQ_F_BCT_006 — tax applied on every realization; gross/net agree
  per net = gross x (1 - rate).
- REQ_F_TAX_002 — net dividend at credit time.
- REQ_F_CAP_014 / REQ_SDD_DAT_001 — stop-loss carried on every Position.
- REQ_SDD_DAT_005 — Trade.fees is the executed fee (consumed directly).
- REQ_SDS_MOD_011 — Portfolio is the integration point between the
  execution layer and analytics / risk.
- PortfolioView Protocol conformance (runtime-checkable).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import InstrumentClass, Stock, Turbo
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket
from trading_system.models.trading import (
    Order,
    OrderType,
    Side,
    StopLoss,
    Trade,
)
from trading_system.portfolio import Portfolio
from trading_system.result import Nothing, Some
from trading_system.strategies.protocol import PortfolioView
from trading_system.tax.config import TaxConfig

EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(year: int = 2026, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _stock(symbol: str = "ASML", iid: str = "ASML.AS") -> Stock:
    return Stock(
        id=InstrumentId(iid),
        symbol=symbol,
        exchange="AS",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _stop(price: str = "50.00") -> StopLoss:
    return StopLoss(price=Decimal(price))


def _order(
    instrument: Stock | Turbo,
    side: Side = Side.BUY,
    qty: str = "10",
    oid: str = "O1",
    stop: str = "50.00",
) -> Order:
    return Order(
        id=OrderId(oid),
        instrument=instrument,
        side=side,
        quantity=Decimal(qty),
        type=OrderType.MARKET,
        stop_loss=_stop(stop),
        created_at=_ts(),
        source_strategy=StrategyId("S1"),
    )


def _trade(  # noqa: PLR0913 — test helper; six args mirror Trade fields
    order: Order,
    price: str,
    qty: str | None = None,
    fees: str = "1.00",
    tid: str = "T1",
    at: datetime | None = None,
) -> Trade:
    return Trade(
        id=TradeId(tid),
        order_id=order.id,
        executed_at=at or _ts(),
        price=Decimal(price),
        quantity_filled=Decimal(qty) if qty is not None else order.quantity,
        fees=_eur(fees),
    )


def _tax() -> TaxConfig:
    return TaxConfig.default()


# ---------------------------------------------------------------------------
# Construction & Protocol conformance
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_starting_capital_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="starting_capital must be > 0"):
            Portfolio.empty(_eur("0"))

    def test_satisfies_portfolio_view_protocol(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        assert isinstance(p, PortfolioView)

    def test_initial_state(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        assert p.cash() == _eur("1000")
        assert p.equity_after_tax() == _eur("1000")
        assert p.realized_gross() == _eur("0")
        assert p.realized_after_tax() == _eur("0")
        assert p.dividends_gross() == _eur("0")
        assert p.dividends_after_tax() == _eur("0")
        assert p.positions() == {}
        assert p.equity_curve == []

    def test_currency_property(self) -> None:
        assert Portfolio.empty(_eur("1000")).currency is EUR


# ---------------------------------------------------------------------------
# Open + mark + equity (REQ_F_PRT_001)
# ---------------------------------------------------------------------------


class TestOpenAndEquity:
    def test_buy_decreases_cash_by_notional_plus_fees(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        s = _stock()
        o = _order(s, qty="10", stop="40.00")
        t = _trade(o, price="50.00", fees="2.00")
        p.apply(t, o, AllocationBucket.STOCK, _tax())
        # cash = 1000 - 50*10 - 2 = 498
        assert p.cash() == _eur("498.00")

    def test_buy_opens_position_with_avg_price_and_bucket(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        s = _stock()
        o = _order(s, qty="10", stop="40.00")
        p.apply(_trade(o, price="50.00"), o, AllocationBucket.STOCK, _tax())
        pos = p.positions()[s.id]
        assert pos.quantity == Decimal("10")
        assert pos.avg_price == Decimal("50.00")
        assert p.holds(s.id) is True
        assert p.position_for(s.id) == Some(pos)

    def test_position_for_returns_nothing_when_absent(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        assert p.position_for(InstrumentId("X")) == Nothing()

    def test_equity_marks_to_market(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        s = _stock()
        o = _order(s, qty="10", stop="40.00")
        p.apply(_trade(o, price="50.00", fees="0.00"), o, AllocationBucket.STOCK, _tax())
        # cash = 500, marked = 10 * 60 = 600, no realized -> 1100
        p.mark({s.id: Decimal("60.00")})
        assert p.equity_after_tax() == _eur("1100.00")

    def test_equity_panics_on_missing_mark_after_jump_in_price(self) -> None:
        # The first apply records last_price; clear it to simulate a
        # programmer error: querying equity without refreshing.
        p = Portfolio.empty(_eur("1000"))
        s = _stock()
        o = _order(s, qty="10", stop="40.00")
        p.apply(_trade(o, price="50.00"), o, AllocationBucket.STOCK, _tax())
        # Simulate missing mark by clearing last_prices.
        p._last_prices.clear()  # type: ignore[attr-defined]
        with pytest.raises(AssertionError, match="missing mark price"):
            p.equity_after_tax()


# ---------------------------------------------------------------------------
# Increase, partial close, full close — realization (REQ_F_BCT_006)
# ---------------------------------------------------------------------------


class TestRealization:
    def test_buy_then_buy_recomputes_avg_price(self) -> None:
        p = Portfolio.empty(_eur("10000"))
        s = _stock()
        o1 = _order(s, qty="10", oid="O1", stop="40.00")
        p.apply(_trade(o1, price="50.00", fees="0.00"), o1, AllocationBucket.STOCK, _tax())
        o2 = _order(s, qty="10", oid="O2", stop="40.00")
        p.apply(
            _trade(o2, price="60.00", fees="0.00", tid="T2"),
            o2,
            AllocationBucket.STOCK,
            _tax(),
        )
        pos = p.positions()[s.id]
        assert pos.quantity == Decimal("20")
        # Weighted: (10*50 + 10*60) / 20 = 55
        assert pos.avg_price == Decimal("55")

    def test_partial_close_realizes_gross_and_after_tax(self) -> None:
        p = Portfolio.empty(_eur("10000"))
        s = _stock()
        o_buy = _order(s, qty="10", oid="OB", stop="40.00")
        p.apply(
            _trade(o_buy, price="50.00", fees="0.00"),
            o_buy,
            AllocationBucket.STOCK,
            _tax(),
        )
        o_sell = _order(s, side=Side.SELL, qty="4", oid="OS", stop="40.00")
        p.apply(
            _trade(o_sell, price="60.00", fees="0.00", tid="T2"),
            o_sell,
            AllocationBucket.STOCK,
            _tax(),
        )
        # Realized gross = 4 * (60 - 50) = 40
        assert p.realized_gross() == _eur("40")
        # Net = 40 * 0.70 = 28.00
        assert p.realized_after_tax() == _eur("28.00")
        # Position remains 6 long at avg 50
        pos = p.positions()[s.id]
        assert pos.quantity == Decimal("6")
        assert pos.avg_price == Decimal("50.00")

    def test_full_close_removes_position(self) -> None:
        p = Portfolio.empty(_eur("10000"))
        s = _stock()
        o_buy = _order(s, qty="10", oid="OB", stop="40.00")
        p.apply(_trade(o_buy, price="50.00", fees="0.00"), o_buy, AllocationBucket.STOCK, _tax())
        o_sell = _order(s, side=Side.SELL, qty="10", oid="OS", stop="40.00")
        p.apply(
            _trade(o_sell, price="55.00", fees="0.00", tid="T2"),
            o_sell,
            AllocationBucket.STOCK,
            _tax(),
        )
        assert p.holds(s.id) is False
        # Realized gross = 10 * (55 - 50) = 50; net = 35.00
        assert p.realized_gross() == _eur("50")
        assert p.realized_after_tax() == _eur("35.00")

    def test_loss_passes_through_untaxed(self) -> None:
        p = Portfolio.empty(_eur("10000"))
        s = _stock()
        o_buy = _order(s, qty="10", oid="OB", stop="40.00")
        p.apply(_trade(o_buy, price="50.00", fees="0.00"), o_buy, AllocationBucket.STOCK, _tax())
        o_sell = _order(s, side=Side.SELL, qty="10", oid="OS", stop="40.00")
        p.apply(
            _trade(o_sell, price="40.00", fees="0.00", tid="T2"),
            o_sell,
            AllocationBucket.STOCK,
            _tax(),
        )
        # Loss = -100; passes through to net (REQ_F_TAX_001 loss handling).
        assert p.realized_gross() == _eur("-100")
        assert p.realized_after_tax() == _eur("-100.00")

    def test_overshoot_flips_direction(self) -> None:
        # Long 10; sell 15 -> realize on 10, open short 5 at sell price.
        p = Portfolio.empty(_eur("10000"))
        s = _stock()
        o_buy = _order(s, qty="10", oid="OB", stop="40.00")
        p.apply(_trade(o_buy, price="50.00", fees="0.00"), o_buy, AllocationBucket.STOCK, _tax())
        o_sell = _order(s, side=Side.SELL, qty="15", oid="OS", stop="40.00")
        p.apply(
            _trade(o_sell, price="55.00", fees="0.00", tid="T2"),
            o_sell,
            AllocationBucket.STOCK,
            _tax(),
        )
        # Realized gross = 10 * (55 - 50) = 50.
        assert p.realized_gross() == _eur("50")
        pos = p.positions()[s.id]
        assert pos.quantity == Decimal("-5")
        assert pos.avg_price == Decimal("55")


# ---------------------------------------------------------------------------
# Exposure (REQ_F_PRT_003)
# ---------------------------------------------------------------------------


class TestExposure:
    def test_zero_for_empty_portfolio(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        assert p.exposure_pct(AllocationBucket.STOCK) == Decimal(0)

    def test_stock_bucket_share(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        s = _stock()
        o = _order(s, qty="10", stop="40.00")
        p.apply(_trade(o, price="50.00", fees="0.00"), o, AllocationBucket.STOCK, _tax())
        # cash = 500, marked at 50 = 500, equity = 1000, exposure = 500/1000 = 0.5
        assert p.exposure_pct(AllocationBucket.STOCK) == Decimal("0.5")
        assert p.exposure_pct(AllocationBucket.TACTICAL) == Decimal(0)
        assert p.exposure_pct(AllocationBucket.TURBO) == Decimal(0)

    def test_tactical_and_stock_buckets_separate(self) -> None:
        p = Portfolio.empty(_eur("2000"))
        s1 = _stock(symbol="A", iid="A.AS")
        s2 = _stock(symbol="B", iid="B.AS")
        o1 = _order(s1, qty="10", oid="O1", stop="40.00")
        o2 = _order(s2, qty="10", oid="O2", stop="40.00")
        p.apply(_trade(o1, price="50.00", fees="0.00"), o1, AllocationBucket.STOCK, _tax())
        p.apply(
            _trade(o2, price="50.00", fees="0.00", tid="T2"),
            o2,
            AllocationBucket.TACTICAL,
            _tax(),
        )
        # cash = 1000, marked = 1000; equity = 2000
        # STOCK marked 500 => 0.25; TACTICAL marked 500 => 0.25
        assert p.exposure_pct(AllocationBucket.STOCK) == Decimal("0.25")
        assert p.exposure_pct(AllocationBucket.TACTICAL) == Decimal("0.25")


# ---------------------------------------------------------------------------
# Dividends (REQ_F_BCT_005, REQ_F_TAX_002)
# ---------------------------------------------------------------------------


class TestDividends:
    def test_credits_net_dividend_to_cash(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        s = _stock()
        o = _order(s, qty="10", stop="40.00")
        p.apply(_trade(o, price="50.00", fees="0.00"), o, AllocationBucket.STOCK, _tax())
        # Cash now 500; credit gross 100 => net 70.
        p.apply_dividend(s.id, _eur("100"), _tax())
        assert p.cash() == _eur("570.00")
        assert p.dividends_gross() == _eur("100")
        assert p.dividends_after_tax() == _eur("70.00")

    def test_panics_when_not_held(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        with pytest.raises(AssertionError, match="not held long"):
            p.apply_dividend(InstrumentId("ASML.AS"), _eur("100"), _tax())


# ---------------------------------------------------------------------------
# Equity curve / drawdown
# ---------------------------------------------------------------------------


class TestEquityCurve:
    def test_record_equity_appends_point(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        s = _stock()
        o = _order(s, qty="10", stop="40.00")
        p.apply(_trade(o, price="50.00", fees="0.00"), o, AllocationBucket.STOCK, _tax())
        p.mark({s.id: Decimal("50.00")})
        point = p.record_equity(_ts(2026, 1, 2))
        assert point.equity_after_tax == _eur("1000.00")
        assert point.drawdown_pct == Decimal(0)
        assert p.equity_curve == [point]

    def test_drawdown_against_running_peak(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        s = _stock()
        o = _order(s, qty="10", stop="40.00")
        p.apply(_trade(o, price="50.00", fees="0.00"), o, AllocationBucket.STOCK, _tax())
        p.mark({s.id: Decimal("60.00")})
        # Equity = 500 + 600 = 1100 -> peak.
        p.record_equity(_ts(2026, 1, 2))
        p.mark({s.id: Decimal("44.00")})
        # Equity = 500 + 440 = 940; dd = (1100 - 940) / 1100 = 0.1454...
        point = p.record_equity(_ts(2026, 1, 3))
        expected = (Decimal("1100") - Decimal("940")) / Decimal("1100")
        assert point.drawdown_pct == expected
