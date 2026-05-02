"""Tests for ``trading_system.execution.fees``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.execution.fees import FeeModel, FlatFeeModel
from trading_system.models.identifiers import InstrumentId, OrderId, StrategyId
from trading_system.models.instrument import Instrument, InstrumentClass
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Order, OrderType, Side, StopLoss

EUR = Currency.EUR


def order(qty: str = "10") -> Order:
    return Order(
        id=OrderId("o1"),
        instrument=Instrument(
            id=InstrumentId("ABC"),
            symbol="ABC",
            exchange="EPA",
            currency=EUR,
            cls=InstrumentClass.STOCK,
        ),
        side=Side.BUY,
        quantity=Decimal(qty),
        type=OrderType.MARKET,
        stop_loss=StopLoss(price=Decimal("90")),
        created_at=datetime(2026, 5, 1),
        source_strategy=StrategyId("s"),
    )


class TestFlatFeeModel:
    def test_protocol_isinstance(self) -> None:
        # REQ_SDD_API_002: runtime-checkable Protocol.
        m = FlatFeeModel(commission=Money(Decimal("1"), EUR), spread_bps=Decimal(0))
        assert isinstance(m, FeeModel)

    def test_pure_commission(self) -> None:
        m = FlatFeeModel(commission=Money(Decimal("1.50"), EUR), spread_bps=Decimal(0))
        fee = m.fees(order(qty="10"), fill_price=Decimal("100"))
        assert fee == Money(Decimal("1.50"), EUR)

    def test_pure_spread(self) -> None:
        # 10 shares * 100 = 1000 notional; 10 bps = 0.1% = 1.0 EUR.
        m = FlatFeeModel(commission=Money(Decimal(0), EUR), spread_bps=Decimal(10))
        fee = m.fees(order(qty="10"), fill_price=Decimal("100"))
        assert fee == Money(Decimal("1.0"), EUR)

    def test_combined(self) -> None:
        m = FlatFeeModel(commission=Money(Decimal("2"), EUR), spread_bps=Decimal(5))
        # notional = 1000, spread = 0.5; total = 2.50.
        fee = m.fees(order(qty="10"), fill_price=Decimal("100"))
        assert fee == Money(Decimal("2.5"), EUR)

    def test_negative_commission_rejected(self) -> None:
        with pytest.raises(ValueError, match="commission must be >= 0"):
            FlatFeeModel(commission=Money(Decimal("-1"), EUR), spread_bps=Decimal(0))

    def test_negative_spread_rejected(self) -> None:
        with pytest.raises(ValueError, match="spread_bps must be >= 0"):
            FlatFeeModel(commission=Money(Decimal(0), EUR), spread_bps=Decimal(-1))

    def test_currency_mismatch_panics(self) -> None:
        m = FlatFeeModel(
            commission=Money(Decimal("1"), Currency.USD),
            spread_bps=Decimal(0),
        )
        with pytest.raises(AssertionError, match="currency"):
            m.fees(order(), fill_price=Decimal("100"))
