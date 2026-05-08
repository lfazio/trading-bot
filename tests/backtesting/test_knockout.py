"""Tests for ``trading_system.backtesting.knockout``.

Covers TC_BCT_004 (knockout simulator closes turbo at zero on barrier
breach) and REQ_F_TRB_005 (loss capped at invested capital).

REQ refs:
- REQ_F_BCT_004 — knockout closes the position at zero.
- REQ_F_TRB_005 — turbo loss is capped at invested capital
  (cost basis); tested by verifying realized_gross == -avg_price * qty
  and that cash does not drop further on knockout.
- REQ_F_TRB_006 — Turbo metadata fields used (knockout, direction).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.backtesting.knockout import KnockoutSimulator
from trading_system.execution.types import Tick
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
from trading_system.portfolio.portfolio import Portfolio
from trading_system.tax.config import TaxConfig

EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


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


def _long_turbo(underlying: InstrumentId, knockout: str = "90") -> Turbo:
    return Turbo(
        id=InstrumentId("T-LONG"),
        symbol="T-LONG",
        exchange="DE",
        currency=EUR,
        cls=InstrumentClass.TURBO,
        underlying=underlying,
        direction="LONG",
        leverage=Decimal("5"),
        knockout=Decimal(knockout),
        spread_pct=Decimal("0"),
    )


def _short_turbo(underlying: InstrumentId, knockout: str = "110") -> Turbo:
    return Turbo(
        id=InstrumentId("T-SHORT"),
        symbol="T-SHORT",
        exchange="DE",
        currency=EUR,
        cls=InstrumentClass.TURBO,
        underlying=underlying,
        direction="SHORT",
        leverage=Decimal("5"),
        knockout=Decimal(knockout),
        spread_pct=Decimal("0"),
    )


def _open_long(p: Portfolio, instrument: Stock | Turbo, qty: str, price: str) -> None:
    o = Order(
        id=OrderId(f"O-{instrument.id}"),
        instrument=instrument,
        side=Side.BUY,
        quantity=Decimal(qty),
        type=OrderType.MARKET,
        stop_loss=StopLoss(price=Decimal("1")),
        created_at=_ts(1),
        source_strategy=StrategyId("S1"),
    )
    t = Trade(
        id=TradeId(f"T-{instrument.id}"),
        order_id=o.id,
        executed_at=_ts(1),
        price=Decimal(price),
        quantity_filled=Decimal(qty),
        fees=_eur("0"),
    )
    bucket = (
        AllocationBucket.TURBO
        if instrument.cls is InstrumentClass.TURBO
        else AllocationBucket.STOCK
    )
    p.apply(t, o, bucket, TaxConfig.default())


def _tick(at: datetime, iid: InstrumentId, last: str) -> Tick:
    price = Decimal(last)
    return Tick(at=at, instrument_id=iid, bid=price, ask=price, last=price)


# ---------------------------------------------------------------------------
# Knockout triggers
# ---------------------------------------------------------------------------


class TestLongKnockout:
    def test_breach_closes_at_zero(self) -> None:
        s = _stock()
        turbo = _long_turbo(underlying=s.id, knockout="90")
        p = Portfolio.empty(_eur("10000"))
        _open_long(p, turbo, qty="10", price="100")
        # Tick on the underlying at 90 -> breach (<=).
        sim = KnockoutSimulator()
        closed = sim.maybe_trigger(_tick(_ts(2), s.id, "90"), p, TaxConfig.default())
        assert closed == [turbo.id]
        assert p.holds(turbo.id) is False
        # Loss = -100 * 10 = -1000. Cash unchanged (still 10000 - 1000 = 9000).
        assert p.realized_gross() == _eur("-1000")
        assert p.cash() == _eur("9000")

    def test_above_knockout_stays_open(self) -> None:
        s = _stock()
        turbo = _long_turbo(underlying=s.id, knockout="90")
        p = Portfolio.empty(_eur("10000"))
        _open_long(p, turbo, qty="10", price="100")
        sim = KnockoutSimulator()
        closed = sim.maybe_trigger(_tick(_ts(2), s.id, "91"), p, TaxConfig.default())
        assert closed == []
        assert p.holds(turbo.id) is True


class TestShortKnockout:
    def test_breach_closes_at_zero(self) -> None:
        s = _stock()
        turbo = _short_turbo(underlying=s.id, knockout="110")
        p = Portfolio.empty(_eur("10000"))
        # We hold the turbo long; the turbo itself is short on the underlying.
        _open_long(p, turbo, qty="5", price="20")
        sim = KnockoutSimulator()
        # Underlying rises to 110 -> SHORT turbo breached.
        closed = sim.maybe_trigger(_tick(_ts(2), s.id, "110"), p, TaxConfig.default())
        assert closed == [turbo.id]
        assert p.holds(turbo.id) is False


class TestUnrelatedInstruments:
    def test_tick_on_unrelated_instrument_is_ignored(self) -> None:
        s = _stock(symbol="A", iid="A.AS")
        other = _stock(symbol="B", iid="B.AS")
        turbo = _long_turbo(underlying=s.id, knockout="90")
        p = Portfolio.empty(_eur("10000"))
        _open_long(p, turbo, qty="10", price="100")
        sim = KnockoutSimulator()
        # Tick on B at low price — turbo references A — no knockout.
        closed = sim.maybe_trigger(_tick(_ts(2), other.id, "1"), p, TaxConfig.default())
        assert closed == []
        assert p.holds(turbo.id) is True

    def test_stock_position_is_ignored(self) -> None:
        s = _stock()
        p = Portfolio.empty(_eur("10000"))
        _open_long(p, s, qty="10", price="50")
        sim = KnockoutSimulator()
        closed = sim.maybe_trigger(_tick(_ts(2), s.id, "0.01"), p, TaxConfig.default())
        # Stock positions never knock out.
        assert closed == []
        assert p.holds(s.id) is True
