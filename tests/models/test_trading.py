"""Tests for ``trading_system.models.trading``.

Verifies stop-loss mandatoriness (REQ_F_CAP_014), positive-magnitude
quantity (REQ_SDD_DAT_006), Trade.fees-as-actual (REQ_SDD_DAT_005),
LIMIT order semantics, dividend invariants.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import Instrument, InstrumentClass
from trading_system.models.money import Currency, Money
from trading_system.models.trading import (
    Dividend,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    StopLoss,
    Trade,
)

EUR = Currency.EUR


def stock(symbol: str = "ABC") -> Instrument:
    return Instrument(
        id=InstrumentId(f"id-{symbol}"),
        symbol=symbol,
        exchange="EPA",
        currency=EUR,
        cls=InstrumentClass.STOCK,
    )


def stop(price: Decimal = Decimal("90")) -> StopLoss:
    return StopLoss(price=price)


def now() -> datetime:
    return datetime(2026, 5, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# StopLoss
# ---------------------------------------------------------------------------


class TestStopLoss:
    def test_basic(self) -> None:
        s = StopLoss(price=Decimal("100"))
        assert s.price == Decimal("100")
        assert s.trailing_pct is None

    def test_with_trailing(self) -> None:
        s = StopLoss(price=Decimal("100"), trailing_pct=Decimal("0.05"))
        assert s.trailing_pct == Decimal("0.05")

    def test_zero_price_rejected(self) -> None:
        with pytest.raises(ValueError, match="price must be > 0"):
            StopLoss(price=Decimal(0))

    def test_negative_price_rejected(self) -> None:
        with pytest.raises(ValueError, match="price must be > 0"):
            StopLoss(price=Decimal(-10))

    def test_trailing_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="trailing_pct"):
            StopLoss(price=Decimal(1), trailing_pct=Decimal(0))
        with pytest.raises(ValueError, match="trailing_pct"):
            StopLoss(price=Decimal(1), trailing_pct=Decimal(1))


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


class TestOrder:
    def test_market_order(self) -> None:
        o = Order(
            id=OrderId("o1"),
            instrument=stock(),
            side=Side.BUY,
            quantity=Decimal(10),
            type=OrderType.MARKET,
            stop_loss=stop(),
            created_at=now(),
            source_strategy=StrategyId("core_v1"),
        )
        assert o.side is Side.BUY
        assert o.limit_price is None

    def test_limit_order_requires_price(self) -> None:
        with pytest.raises(ValueError, match="limit_price required"):
            Order(
                id=OrderId("o1"),
                instrument=stock(),
                side=Side.BUY,
                quantity=Decimal(10),
                type=OrderType.LIMIT,
                stop_loss=stop(),
                created_at=now(),
                source_strategy=StrategyId("core_v1"),
            )

    def test_market_order_rejects_limit_price(self) -> None:
        with pytest.raises(ValueError, match="limit_price must be None"):
            Order(
                id=OrderId("o1"),
                instrument=stock(),
                side=Side.BUY,
                quantity=Decimal(10),
                type=OrderType.MARKET,
                limit_price=Decimal(100),
                stop_loss=stop(),
                created_at=now(),
                source_strategy=StrategyId("core_v1"),
            )

    @pytest.mark.parametrize("qty", [Decimal(0), Decimal(-1)])
    def test_non_positive_quantity_rejected(self, qty: Decimal) -> None:
        with pytest.raises(ValueError, match="quantity must be > 0"):
            Order(
                id=OrderId("o1"),
                instrument=stock(),
                side=Side.BUY,
                quantity=qty,
                type=OrderType.MARKET,
                stop_loss=stop(),
                created_at=now(),
                source_strategy=StrategyId("core_v1"),
            )


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------


class TestTrade:
    def test_valid(self) -> None:
        t = Trade(
            id=TradeId("t1"),
            order_id=OrderId("o1"),
            executed_at=now(),
            price=Decimal("100"),
            quantity_filled=Decimal(10),
            fees=Money(Decimal("0.50"), EUR),
        )
        assert t.fees.amount == Decimal("0.50")
        assert t.slippage == Decimal(0)

    @pytest.mark.parametrize("price", [Decimal(0), Decimal("-1")])
    def test_non_positive_price_rejected(self, price: Decimal) -> None:
        with pytest.raises(ValueError, match="price must be > 0"):
            Trade(
                id=TradeId("t1"),
                order_id=OrderId("o1"),
                executed_at=now(),
                price=price,
                quantity_filled=Decimal(10),
                fees=Money(Decimal(0), EUR),
            )

    @pytest.mark.parametrize("qty", [Decimal(0), Decimal("-1")])
    def test_non_positive_quantity_rejected(self, qty: Decimal) -> None:
        with pytest.raises(ValueError, match="quantity_filled must be > 0"):
            Trade(
                id=TradeId("t1"),
                order_id=OrderId("o1"),
                executed_at=now(),
                price=Decimal(100),
                quantity_filled=qty,
                fees=Money(Decimal(0), EUR),
            )


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


class TestPosition:
    def test_long(self) -> None:
        p = Position(
            instrument=stock(),
            quantity=Decimal(10),
            avg_price=Decimal("100"),
            opened_at=now(),
            stop_loss=stop(),
        )
        assert p.quantity == Decimal(10)

    def test_short(self) -> None:
        p = Position(
            instrument=stock(),
            quantity=Decimal(-5),
            avg_price=Decimal("100"),
            opened_at=now(),
            stop_loss=stop(),
        )
        assert p.quantity == Decimal(-5)

    def test_zero_quantity_rejected(self) -> None:
        with pytest.raises(ValueError, match="quantity must be non-zero"):
            Position(
                instrument=stock(),
                quantity=Decimal(0),
                avg_price=Decimal("100"),
                opened_at=now(),
                stop_loss=stop(),
            )

    def test_non_positive_price_rejected(self) -> None:
        with pytest.raises(ValueError, match="avg_price must be > 0"):
            Position(
                instrument=stock(),
                quantity=Decimal(10),
                avg_price=Decimal(0),
                opened_at=now(),
                stop_loss=stop(),
            )


# ---------------------------------------------------------------------------
# Dividend
# ---------------------------------------------------------------------------


class TestDividend:
    def test_valid_gross_only(self) -> None:
        d = Dividend(
            instrument=InstrumentId("ABC"),
            ex_date=datetime(2026, 5, 1),
            pay_date=datetime(2026, 5, 15),
            amount_gross=Money(Decimal("2.50"), EUR),
        )
        assert d.amount_net is None

    def test_valid_with_net(self) -> None:
        d = Dividend(
            instrument=InstrumentId("ABC"),
            ex_date=datetime(2026, 5, 1),
            pay_date=datetime(2026, 5, 15),
            amount_gross=Money(Decimal("2.50"), EUR),
            amount_net=Money(Decimal("1.75"), EUR),
        )
        assert d.amount_net == Money(Decimal("1.75"), EUR)

    def test_zero_gross_rejected(self) -> None:
        with pytest.raises(ValueError, match="amount_gross must be > 0"):
            Dividend(
                instrument=InstrumentId("ABC"),
                ex_date=datetime(2026, 5, 1),
                pay_date=datetime(2026, 5, 1),
                amount_gross=Money(Decimal(0), EUR),
            )

    def test_pay_before_ex_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"pay_date .* must be on or after"):
            Dividend(
                instrument=InstrumentId("ABC"),
                ex_date=datetime(2026, 5, 15),
                pay_date=datetime(2026, 5, 1),
                amount_gross=Money(Decimal("1"), EUR),
            )

    def test_net_currency_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="amount_net currency must match"):
            Dividend(
                instrument=InstrumentId("ABC"),
                ex_date=datetime(2026, 5, 1),
                pay_date=datetime(2026, 5, 15),
                amount_gross=Money(Decimal("2"), EUR),
                amount_net=Money(Decimal("1"), Currency.USD),
            )

    def test_net_exceeding_gross_rejected(self) -> None:
        with pytest.raises(ValueError, match="amount_net cannot exceed amount_gross"):
            Dividend(
                instrument=InstrumentId("ABC"),
                ex_date=datetime(2026, 5, 1),
                pay_date=datetime(2026, 5, 15),
                amount_gross=Money(Decimal("2"), EUR),
                amount_net=Money(Decimal("3"), EUR),
            )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_side_strenum() -> None:
    assert Side.BUY.value == "buy"
    assert Side("sell") is Side.SELL


def test_order_status_strenum() -> None:
    assert OrderStatus.FILLED.value == "filled"
    assert {s for s in OrderStatus} == {
        OrderStatus.PENDING,
        OrderStatus.FILLED,
        OrderStatus.PARTIAL,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    }
