"""Tests for ``trading_system.execution.slippage``."""

from __future__ import annotations

import random
from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.execution.slippage import (
    GaussianSlippageModel,
    SlippageModel,
    ZeroSlippageModel,
)
from trading_system.models.identifiers import InstrumentId, OrderId, StrategyId
from trading_system.models.instrument import Instrument, InstrumentClass
from trading_system.models.money import Currency
from trading_system.models.trading import Order, OrderType, Side, StopLoss

EUR = Currency.EUR


def order(side: Side = Side.BUY) -> Order:
    return Order(
        id=OrderId("o1"),
        instrument=Instrument(
            id=InstrumentId("ABC"),
            symbol="ABC",
            exchange="EPA",
            currency=EUR,
            cls=InstrumentClass.STOCK,
        ),
        side=side,
        quantity=Decimal(10),
        type=OrderType.MARKET,
        stop_loss=StopLoss(price=Decimal("90")),
        created_at=datetime(2026, 5, 1),
        source_strategy=StrategyId("s"),
    )


class TestZeroSlippageModel:
    def test_isinstance(self) -> None:
        assert isinstance(ZeroSlippageModel(), SlippageModel)

    def test_returns_zero(self) -> None:
        m = ZeroSlippageModel()
        rng = random.Random(0)
        assert m.slip(order(), Decimal("100"), rng) == Decimal(0)


class TestGaussianSlippageModel:
    def test_isinstance(self) -> None:
        assert isinstance(GaussianSlippageModel(stdev_pct=Decimal("0.01")), SlippageModel)

    def test_negative_stdev_rejected(self) -> None:
        with pytest.raises(ValueError, match="stdev_pct must be >= 0"):
            GaussianSlippageModel(stdev_pct=Decimal("-0.01"))

    def test_zero_stdev_returns_zero(self) -> None:
        m = GaussianSlippageModel(stdev_pct=Decimal(0))
        rng = random.Random(0)
        assert m.slip(order(), Decimal("100"), rng) == Decimal(0)

    def test_buy_is_positive_or_zero(self) -> None:
        # Half-normal magnitude is non-negative; BUY keeps the positive sign.
        m = GaussianSlippageModel(stdev_pct=Decimal("0.005"))
        rng = random.Random(42)
        for _ in range(50):
            slip = m.slip(order(side=Side.BUY), Decimal("100"), rng)
            assert slip >= 0

    def test_sell_is_negative_or_zero(self) -> None:
        m = GaussianSlippageModel(stdev_pct=Decimal("0.005"))
        rng = random.Random(42)
        for _ in range(50):
            slip = m.slip(order(side=Side.SELL), Decimal("100"), rng)
            assert slip <= 0

    def test_deterministic_same_seed(self) -> None:
        m = GaussianSlippageModel(stdev_pct=Decimal("0.01"))
        rng_a = random.Random(7)
        rng_b = random.Random(7)
        assert m.slip(order(), Decimal("100"), rng_a) == m.slip(order(), Decimal("100"), rng_b)
