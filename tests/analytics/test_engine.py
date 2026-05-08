"""Tests for ``trading_system.analytics.engine``.

REQ refs:
- REQ_F_PRT_002 — attribution rows produced.
- REQ_NF_LOG_001 — analytics surfaces timestamped trades.
- REQ_SDS_MOD_005 — equity_excl_injections is the canonical
  performance series.
- REQ_SDS_MOD_011 — after-tax equity is the canonical reference.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.analytics import Analytics
from trading_system.capital_flow import CapitalFlow
from trading_system.models.flow import Injection
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket
from trading_system.models.trading import Order, OrderType, Side, StopLoss, Trade
from trading_system.portfolio import Portfolio
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


def _build_trade(  # noqa: PLR0913 - test helper; matches Trade + Order surface
    p: Portfolio,
    s: Stock,
    *,
    side: Side,
    qty: str,
    price: str,
    strategy: str = "core_v1",
    fees: str = "1.00",
    oid: str = "O1",
    tid: str = "T1",
    at: datetime | None = None,
) -> Trade:
    o = Order(
        id=OrderId(oid),
        instrument=s,
        side=side,
        quantity=Decimal(qty),
        type=OrderType.MARKET,
        stop_loss=StopLoss(price=Decimal("40")),
        created_at=at or _ts(),
        source_strategy=StrategyId(strategy),
    )
    t = Trade(
        id=TradeId(tid),
        order_id=o.id,
        executed_at=at or _ts(),
        price=Decimal(price),
        quantity_filled=Decimal(qty),
        fees=_eur(fees),
    )
    p.apply(t, o, AllocationBucket.STOCK, TaxConfig.default())
    return t


def _empty_pair() -> tuple[Portfolio, CapitalFlow]:
    p = Portfolio.empty(_eur("10000"))
    cf = CapitalFlow(initial=_eur("10000"))
    return p, cf


# ---------------------------------------------------------------------------
# Series and totals
# ---------------------------------------------------------------------------


class TestSeries:
    def test_empty_curve_yields_empty_series(self) -> None:
        p, cf = _empty_pair()
        a = Analytics(portfolio=p, capital_flow=cf)
        assert a.equity_curve() == ()
        assert a.equity_excl_injections() == ()
        assert a.drawdown_series() == ()
        assert a.max_drawdown() == Decimal(0)

    def test_equity_excl_injections_strips_capital_inflow(self) -> None:
        p = Portfolio.empty(_eur("1000"))
        cf = CapitalFlow(initial=_eur("1000"))
        # Inject 500 mid-period; mark; record.
        cf.observe(Injection(amount=_eur("500"), at=_ts(2026, 1, 5)))
        p.inject(_eur("500"))
        p.record_equity(_ts(2026, 1, 5))
        a = Analytics(portfolio=p, capital_flow=cf)
        # Equity_after_tax = 1500; stripped = 1000.
        assert a.equity_excl_injections() == (Decimal("1000"),)


# ---------------------------------------------------------------------------
# Exposure by class
# ---------------------------------------------------------------------------


class TestExposureByClass:
    def test_stock_position_lumps_under_stock_class(self) -> None:
        s = _stock()
        p = Portfolio.empty(_eur("1000"))
        cf = CapitalFlow(initial=_eur("1000"))
        _build_trade(p, s, side=Side.BUY, qty="10", price="50", fees="0")
        # cash 500 + marked 500 = 1000; stock exposure = 500/1000 = 0.5.
        a = Analytics(portfolio=p, capital_flow=cf)
        exposures = a.exposure_by_class()
        assert exposures[InstrumentClass.STOCK] == Decimal("0.5")
        assert exposures[InstrumentClass.TURBO] == Decimal(0)
        assert exposures[InstrumentClass.STRUCTURED] == Decimal(0)


# ---------------------------------------------------------------------------
# Attribution (REQ_F_PRT_002, TC_PRT_003)
# ---------------------------------------------------------------------------


class TestAttribution:
    def test_nav_row_always_first(self) -> None:
        p, cf = _empty_pair()
        a = Analytics(portfolio=p, capital_flow=cf)
        rows = a.attribution()
        assert rows[0].kind == "nav"

    def test_strategy_row_after_realization(self) -> None:
        s = _stock()
        p, cf = _empty_pair()
        _build_trade(
            p, s, side=Side.BUY, qty="10", price="50", strategy="core_v1", fees="0", oid="OB"
        )
        _build_trade(
            p,
            s,
            side=Side.SELL,
            qty="10",
            price="55",
            strategy="tactical_v1",
            fees="0",
            oid="OS",
            tid="T2",
        )
        a = Analytics(portfolio=p, capital_flow=cf)
        rows = a.attribution()
        # Strategy row attributes the realized PnL to the OPENER ("core_v1"),
        # not the closer.
        strategy_rows = [r for r in rows if r.kind == "strategy"]
        assert len(strategy_rows) == 1
        assert strategy_rows[0].label == "core_v1"
        # Gross 50 -> Net 35.00
        assert strategy_rows[0].realized_gross == _eur("50")
        assert strategy_rows[0].realized_after_tax == _eur("35.00")

    def test_class_row_aggregates_realized_pnl(self) -> None:
        s = _stock()
        p, cf = _empty_pair()
        _build_trade(p, s, side=Side.BUY, qty="10", price="50", fees="0", oid="OB")
        _build_trade(p, s, side=Side.SELL, qty="10", price="55", fees="0", oid="OS", tid="T2")
        a = Analytics(portfolio=p, capital_flow=cf)
        class_rows = [r for r in a.attribution() if r.kind == "class"]
        assert len(class_rows) == 1
        assert class_rows[0].label == "stock"
        assert class_rows[0].realized_gross == _eur("50")


# ---------------------------------------------------------------------------
# PerformanceSummary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_carries_realized_and_dividends_totals(self) -> None:
        s = _stock()
        p, cf = _empty_pair()
        t1 = _build_trade(p, s, side=Side.BUY, qty="10", price="50", fees="1.00", oid="OB")
        t2 = _build_trade(
            p, s, side=Side.SELL, qty="10", price="55", fees="1.00", oid="OS", tid="T2"
        )
        a = Analytics(portfolio=p, capital_flow=cf, trades=(t1, t2))
        summary = a.summary()
        assert summary.realized_gross == _eur("50")
        assert summary.realized_after_tax == _eur("35.00")
        assert summary.fees_total == _eur("2.00")
        assert summary.trade_count == 2

    def test_total_return_after_tax_zero_on_empty(self) -> None:
        p, cf = _empty_pair()
        a = Analytics(portfolio=p, capital_flow=cf)
        assert a.summary().total_return_after_tax_pct == Decimal(0)
