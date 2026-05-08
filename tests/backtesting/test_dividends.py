"""Tests for ``trading_system.backtesting.dividends``.

Covers TC_BCT_005 (dividend simulator credits net amount at pay date).

REQ refs: REQ_F_BCT_005, REQ_F_TAX_002.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.backtesting.dividends import DividendSimulator
from trading_system.data.mock import MockMarketDataProvider
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket
from trading_system.models.trading import (
    Dividend,
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


def _stock() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _build_long_position(p: Portfolio, s: Stock, qty: str = "10") -> None:
    o = Order(
        id=OrderId("OB"),
        instrument=s,
        side=Side.BUY,
        quantity=Decimal(qty),
        type=OrderType.MARKET,
        stop_loss=StopLoss(price=Decimal("40")),
        created_at=_ts(1),
        source_strategy=StrategyId("S1"),
    )
    t = Trade(
        id=TradeId("T1"),
        order_id=o.id,
        executed_at=_ts(1),
        price=Decimal("50"),
        quantity_filled=Decimal(qty),
        fees=_eur("0"),
    )
    p.apply(t, o, AllocationBucket.STOCK, TaxConfig.default())


class TestMaybeApply:
    def test_credits_per_share_dividend_at_pay_date(self) -> None:
        # 0.50 per share gross x 10 shares = 5 gross; net = 3.50.
        s = _stock()
        data = MockMarketDataProvider(seed=1)
        data.register_dividend(
            Dividend(
                instrument=s.id,
                ex_date=_ts(15),
                pay_date=_ts(20),
                amount_gross=_eur("0.50"),
            )
        )
        p = Portfolio.empty(_eur("1000"))
        _build_long_position(p, s)
        # cash dropped by 50*10 = 500 after the buy fill.
        assert p.cash() == _eur("500")
        sim = DividendSimulator(data=data)
        applied = sim.maybe_apply(_ts(20), p, TaxConfig.default())
        assert len(applied) == 1
        # cash credited GROSS 5; tax liability tracked separately.
        assert p.cash() == _eur("505")
        assert p.dividends_gross() == _eur("5.00")
        assert p.dividends_after_tax() == _eur("3.50")

    def test_no_credit_before_pay_date(self) -> None:
        s = _stock()
        data = MockMarketDataProvider(seed=1)
        data.register_dividend(
            Dividend(
                instrument=s.id,
                ex_date=_ts(15),
                pay_date=_ts(20),
                amount_gross=_eur("0.50"),
            )
        )
        p = Portfolio.empty(_eur("1000"))
        _build_long_position(p, s)
        sim = DividendSimulator(data=data)
        assert sim.maybe_apply(_ts(15), p, TaxConfig.default()) == []
        assert p.dividends_gross() == _eur("0")

    def test_no_credit_when_not_held(self) -> None:
        s = _stock()
        data = MockMarketDataProvider(seed=1)
        data.register_dividend(
            Dividend(
                instrument=s.id,
                ex_date=_ts(15),
                pay_date=_ts(20),
                amount_gross=_eur("0.50"),
            )
        )
        p = Portfolio.empty(_eur("1000"))  # nothing held
        sim = DividendSimulator(data=data)
        assert sim.maybe_apply(_ts(20), p, TaxConfig.default()) == []
        assert p.dividends_gross() == _eur("0")

    def test_idempotent_within_one_run(self) -> None:
        # Two calls at the same pay_date should credit only once.
        s = _stock()
        data = MockMarketDataProvider(seed=1)
        data.register_dividend(
            Dividend(
                instrument=s.id,
                ex_date=_ts(15),
                pay_date=_ts(20),
                amount_gross=_eur("0.50"),
            )
        )
        p = Portfolio.empty(_eur("1000"))
        _build_long_position(p, s)
        sim = DividendSimulator(data=data)
        sim.maybe_apply(_ts(20), p, TaxConfig.default())
        sim.maybe_apply(_ts(20), p, TaxConfig.default())
        assert p.dividends_gross() == _eur("5.00")
