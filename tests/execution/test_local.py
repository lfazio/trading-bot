"""Tests for ``trading_system.execution.local`` (LocalBrokerAdapter).

Exercises the BrokerAdapter Protocol surface plus the simulator
internals: tick-driven fills, fees + slippage application, position
open/close/flip, idempotency, and Account derivation.

REQ refs:
- REQ_F_BRK_001..005 — BrokerAdapter Protocol shape + concrete impl.
- REQ_TP_STR_004 — Concrete BrokerAdapter implementations SHALL
  pass this conformance test suite. ``LocalBrokerAdapter`` is the
  shipped baseline; future live-broker adapters MAY ship only
  after these tests pass against them. The Protocol-conformance
  test below (``test_local_broker_adapter_satisfies_protocol``)
  is the gate any new adapter SHALL exercise.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.execution.adapter import BrokerAdapter, Subscription
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.slippage import ZeroSlippageModel
from trading_system.execution.types import Tick
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
)
from trading_system.models.instrument import Instrument, InstrumentClass
from trading_system.models.money import Currency, Money
from trading_system.models.trading import (
    Order,
    OrderType,
    Side,
    StopLoss,
)
from trading_system.result import Err, Nothing, Ok, Some

EUR = Currency.EUR
USD = Currency.USD


def stock(symbol: str = "ABC") -> Instrument:
    return Instrument(
        id=InstrumentId(f"id-{symbol}"),
        symbol=symbol,
        exchange="EPA",
        currency=EUR,
        cls=InstrumentClass.STOCK,
    )


def order(  # noqa: PLR0913 - test factory mirrors Order's required fields
    *,
    id_: str = "o1",
    side: Side = Side.BUY,
    qty: str = "10",
    type_: OrderType = OrderType.MARKET,
    instrument: Instrument | None = None,
    limit_price: Decimal | None = None,
) -> Order:
    return Order(
        id=OrderId(id_),
        instrument=instrument if instrument is not None else stock(),
        side=side,
        quantity=Decimal(qty),
        type=type_,
        limit_price=limit_price,
        stop_loss=StopLoss(price=Decimal("90")),
        created_at=datetime(2026, 5, 1, 9, 30),
        source_strategy=StrategyId("core_v1"),
    )


def tick(symbol: str = "ABC", **overrides: object) -> Tick:
    base: dict[str, object] = {
        "at": datetime(2026, 5, 1, 9, 30),
        "instrument_id": InstrumentId(f"id-{symbol}"),
        "bid": Decimal("100.00"),
        "ask": Decimal("100.10"),
        "last": Decimal("100.05"),
    }
    base.update(overrides)
    return Tick(**base)  # type: ignore[arg-type]


def adapter(starting_cash: str = "10000") -> LocalBrokerAdapter:
    return LocalBrokerAdapter(
        starting_cash=Money(Decimal(starting_cash), EUR),
        fee_model=FlatFeeModel(commission=Money(Decimal("0"), EUR), spread_bps=Decimal(0)),
        slippage_model=ZeroSlippageModel(),
        seed=0,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_isinstance_broker(self) -> None:
        assert isinstance(adapter(), BrokerAdapter)


# ---------------------------------------------------------------------------
# Submit — happy paths and rejections
# ---------------------------------------------------------------------------


class TestSubmitMarket:
    def test_buy_at_ask(self) -> None:
        a = adapter()
        a.process_tick(tick())
        result = a.submit(order(side=Side.BUY))
        match result:
            case Ok(oid):
                assert oid == "o1"
            case Err(reason):
                pytest.fail(f"unexpected Err: {reason}")
        # Cash decreases by 10 * 100.10 = 1001.00.
        assert a.account_state().cash == Money(Decimal("8999.00"), EUR)
        positions = a.positions()
        assert len(positions) == 1
        assert positions[0].quantity == Decimal(10)
        assert positions[0].avg_price == Decimal("100.10")

    def test_sell_at_bid(self) -> None:
        a = adapter()
        a.process_tick(tick())
        # Open a short by selling without a prior position (shorting allowed
        # in this simulator since margin is not modelled — REQ_F_TRB_005
        # applies to turbos specifically).
        result = a.submit(order(side=Side.SELL))
        assert isinstance(result, Ok)
        positions = a.positions()
        assert positions[0].quantity == Decimal(-10)
        assert positions[0].avg_price == Decimal("100.00")

    def test_submit_without_tick_returns_err(self) -> None:
        a = adapter()
        match a.submit(order()):
            case Err(reason):
                assert reason.startswith("broker:no_market_data")
            case Ok(_):
                pytest.fail("expected Err")

    def test_unsupported_order_type_returns_err(self) -> None:
        a = adapter()
        a.process_tick(tick())
        result = a.submit(
            order(type_=OrderType.LIMIT, limit_price=Decimal("99")),
        )
        match result:
            case Err(reason):
                assert reason.startswith("broker:order_unsupported")
            case Ok(_):
                pytest.fail("expected Err")

    def test_currency_mismatch_returns_err(self) -> None:
        a = adapter()
        usd_stock = Instrument(
            id=InstrumentId("USD-1"),
            symbol="USD1",
            exchange="NYS",
            currency=USD,
            cls=InstrumentClass.STOCK,
        )
        a.process_tick(tick(instrument_id=usd_stock.id, last=Decimal("100.05")))
        result = a.submit(order(instrument=usd_stock))
        match result:
            case Err(reason):
                assert reason.startswith("broker:currency_mismatch")
            case Ok(_):
                pytest.fail("expected Err")

    def test_idempotent_resubmit(self) -> None:
        # REQ_SDD_API_006: same client id returns the same OrderId without re-filling.
        a = adapter()
        a.process_tick(tick())
        first = a.submit(order())
        second = a.submit(order())
        assert first == second
        # Only one position should exist (one fill).
        assert a.positions()[0].quantity == Decimal(10)


# ---------------------------------------------------------------------------
# Fees / slippage applied
# ---------------------------------------------------------------------------


class TestFeesAndSlippage:
    def test_commission_charged(self) -> None:
        a = LocalBrokerAdapter(
            starting_cash=Money(Decimal(10000), EUR),
            fee_model=FlatFeeModel(commission=Money(Decimal("1.50"), EUR), spread_bps=Decimal(0)),
            slippage_model=ZeroSlippageModel(),
            seed=0,
        )
        a.process_tick(tick())
        a.submit(order())
        # Cash: 10000 - 10*100.10 - 1.50 = 8997.50
        assert a.account_state().cash == Money(Decimal("8997.50"), EUR)


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_unknown_order_returns_err(self) -> None:
        a = adapter()
        match a.cancel(OrderId("nope")):
            case Err(reason):
                assert reason.startswith("broker:not_found")
            case Ok(_):
                pytest.fail("expected Err")

    def test_cancel_filled_returns_err(self) -> None:
        a = adapter()
        a.process_tick(tick())
        a.submit(order())
        match a.cancel(OrderId("o1")):
            case Err(reason):
                assert reason.startswith("broker:already_filled")
            case Ok(_):
                pytest.fail("expected Err")

    def test_cancel_idempotent_after_first_cancel(self) -> None:
        # Without LIMIT/STOP support yet there is no easy way to land
        # a non-filled order into the book. Smoke-test the "twice
        # cancelled" branch by manually adding to _cancelled.
        a = adapter()
        a._orders[OrderId("queued")] = order(
            id_="queued", type_=OrderType.LIMIT, limit_price=Decimal("99")
        )
        first = a.cancel(OrderId("queued"))
        second = a.cancel(OrderId("queued"))
        assert first == Ok(True)
        assert second == Ok(False)


# ---------------------------------------------------------------------------
# Positions: open, average up, close, flip
# ---------------------------------------------------------------------------


class TestPositionTransitions:
    def test_average_up(self) -> None:
        a = adapter()
        a.process_tick(tick(last=Decimal("100.05"), bid=Decimal("100.00"), ask=Decimal("100.10")))
        a.submit(order(id_="o1"))
        a.process_tick(tick(last=Decimal("110.05"), bid=Decimal("110.00"), ask=Decimal("110.10")))
        a.submit(order(id_="o2"))
        positions = a.positions()
        assert len(positions) == 1
        assert positions[0].quantity == Decimal(20)
        # Avg = (10*100.10 + 10*110.10) / 20 = 105.10
        assert positions[0].avg_price == Decimal("105.10")

    def test_full_close_realizes_pnl(self) -> None:
        a = adapter()
        a.process_tick(tick(last=Decimal("100.05"), bid=Decimal("100.00"), ask=Decimal("100.10")))
        a.submit(order(id_="o1", side=Side.BUY))
        a.process_tick(tick(last=Decimal("110.05"), bid=Decimal("110.00"), ask=Decimal("110.10")))
        a.submit(order(id_="o2", side=Side.SELL))
        # Realized PnL = (110.00 - 100.10) * 10 = 99.00.
        assert a.account_state().realized_pnl == Money(Decimal("99.00"), EUR)
        assert a.positions() == []

    def test_partial_close(self) -> None:
        a = adapter()
        a.process_tick(tick(last=Decimal("100.05"), bid=Decimal("100.00"), ask=Decimal("100.10")))
        a.submit(order(id_="o1", side=Side.BUY, qty="10"))
        a.process_tick(tick(last=Decimal("110.05"), bid=Decimal("110.00"), ask=Decimal("110.10")))
        a.submit(order(id_="o2", side=Side.SELL, qty="3"))
        positions = a.positions()
        assert positions[0].quantity == Decimal(7)
        # Realized = (110.00 - 100.10) * 3 = 29.70
        assert a.account_state().realized_pnl == Money(Decimal("29.70"), EUR)

    def test_flip_long_to_short(self) -> None:
        a = adapter()
        a.process_tick(tick(last=Decimal("100.05"), bid=Decimal("100.00"), ask=Decimal("100.10")))
        a.submit(order(id_="o1", side=Side.BUY, qty="10"))
        a.process_tick(tick(last=Decimal("110.05"), bid=Decimal("110.00"), ask=Decimal("110.10")))
        a.submit(order(id_="o2", side=Side.SELL, qty="15"))
        positions = a.positions()
        # Closed long of 10, opened short of 5.
        assert positions[0].quantity == Decimal(-5)
        assert positions[0].avg_price == Decimal("110.00")
        # Realized on the long: (110 - 100.10) * 10 = 99.00
        assert a.account_state().realized_pnl == Money(Decimal("99.00"), EUR)


# ---------------------------------------------------------------------------
# instrument()
# ---------------------------------------------------------------------------


class TestInstrumentLookup:
    def test_unknown_returns_nothing(self) -> None:
        a = adapter()
        assert a.instrument("UNKNOWN") == Nothing()

    def test_registered_returns_some(self) -> None:
        a = adapter()
        s = stock()
        a.register_instrument(s)
        assert a.instrument("ABC") == Some(s)


# ---------------------------------------------------------------------------
# subscribe()
# ---------------------------------------------------------------------------


class TestSubscribe:
    def test_callback_invoked_for_matching_symbol(self) -> None:
        a = adapter()
        a.register_instrument(stock())
        seen: list[Tick] = []
        sub = a.subscribe(["ABC"], seen.append)
        assert isinstance(sub, Subscription)
        a.process_tick(tick())
        assert len(seen) == 1

    def test_wildcard_matches_all(self) -> None:
        a = adapter()
        a.register_instrument(stock("ABC"))
        a.register_instrument(stock("XYZ"))
        seen: list[Tick] = []
        a.subscribe(["*"], seen.append)
        a.process_tick(tick("ABC"))
        a.process_tick(tick("XYZ"))
        assert len(seen) == 2

    def test_cancel_stops_callbacks(self) -> None:
        a = adapter()
        a.register_instrument(stock())
        seen: list[Tick] = []
        sub = a.subscribe(["ABC"], seen.append)
        a.process_tick(tick())
        sub.cancel()
        a.process_tick(tick())
        assert len(seen) == 1

    def test_cancel_idempotent(self) -> None:
        a = adapter()
        sub = a.subscribe(["ABC"], lambda _: None)
        sub.cancel()
        sub.cancel()  # no exception

    def test_unmatched_symbol_does_not_invoke(self) -> None:
        a = adapter()
        a.register_instrument(stock("ABC"))
        a.register_instrument(stock("XYZ"))
        seen: list[Tick] = []
        a.subscribe(["XYZ"], seen.append)
        a.process_tick(tick("ABC"))
        assert seen == []


# ---------------------------------------------------------------------------
# account_state derivation
# ---------------------------------------------------------------------------


class TestAccountState:
    def test_equity_marks_to_market(self) -> None:
        a = adapter()
        a.process_tick(tick(last=Decimal("100.05"), bid=Decimal("100.00"), ask=Decimal("100.10")))
        a.submit(order(qty="10"))
        # Mark = 100.05; positions value = 10 * 100.05 = 1000.50
        # Cash = 10000 - 10 * 100.10 = 8999.00
        # Equity = 9999.50
        a_state = a.account_state()
        assert a_state.equity == Money(Decimal("9999.50"), EUR)
        # Unrealized = 10 * (100.05 - 100.10) = -0.50
        assert a_state.unrealized_pnl == Money(Decimal("-0.50"), EUR)


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_negative_starting_cash_rejected(self) -> None:
        with pytest.raises(ValueError, match="starting_cash must be >= 0"):
            LocalBrokerAdapter(
                starting_cash=Money(Decimal(-1), EUR),
                fee_model=FlatFeeModel(commission=Money(Decimal(0), EUR), spread_bps=Decimal(0)),
                slippage_model=ZeroSlippageModel(),
            )
