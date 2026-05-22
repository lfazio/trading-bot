"""Broker-adapter conformance suite — Phase 6 operational gate.

REQ_TP_STR_004 — Concrete ``BrokerAdapter`` implementations
SHALL pass the same conformance test suite as
``LocalBrokerAdapter``. New adapters MAY ship only after passing
this suite. The drill below runs the documented contract
end-to-end against ``LocalBrokerAdapter`` (the shipped baseline)
so future live-broker adapters have an explicit golden reference
they must match.

The suite covers every method on the ``BrokerAdapter`` Protocol:

- ``submit(order)`` — Ok(OrderId) on fill; categorised Errs
  (``broker:no_market_data``, ``broker:order_unsupported``,
  ``broker:rejected``, ``broker:currency_mismatch``) on failure.
- ``cancel(order_id)`` — Ok(True) on fresh cancel, Ok(False)
  on idempotent re-cancel, Err on filled / unknown.
- ``positions()`` — list[Position] reflecting current state.
- ``account_state()`` — Account snapshot with cash + realized
  PnL + unrealized PnL + equity.
- ``instrument(symbol)`` — Some/Nothing lookup.
- ``subscribe(symbols, on_tick)`` — Subscription handle; cancel
  is idempotent.

REQ refs:
- REQ_F_BRK_001..005 — full Protocol surface.
- REQ_SDD_API_006 — submit / cancel idempotent on duplicate
  client ids.
- REQ_SDS_INT_001 — runtime-checkable Protocol.
- REQ_SDD_API_002 — Protocol marker.
- REQ_TP_STR_004 — this file IS the conformance gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from trading_system.models.instrument import Instrument, InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.trading import (
    Order,
    OrderType,
    Side,
    StopLoss,
)
from trading_system.result import Err, Nothing, Ok, Some


_EUR = Currency.EUR


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=_EUR)


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, tzinfo=UTC)


def _stock(symbol: str = "ASML", iid: str | None = None) -> Stock:
    return Stock(
        id=InstrumentId(iid or f"{symbol}.AS"),
        symbol=symbol,
        exchange="AS",
        currency=_EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _order(
    *,
    instrument: Instrument | None = None,
    side: Side = Side.BUY,
    quantity: Decimal = Decimal("10"),
    oid: str = "o-1",
    order_type: OrderType = OrderType.MARKET,
) -> Order:
    return Order(
        id=OrderId(oid),
        instrument=instrument or _stock(),
        side=side,
        quantity=quantity,
        type=order_type,
        stop_loss=StopLoss(price=Decimal("9")),
        created_at=_ts(1),
        source_strategy=StrategyId("test"),
    )


def _tick(instrument: Instrument, *, last: str = "100") -> Tick:
    return Tick(
        at=_ts(1),
        instrument_id=instrument.id,
        bid=Decimal(last) - Decimal("1"),
        ask=Decimal(last) + Decimal("1"),
        last=Decimal(last),
    )


@pytest.fixture
def adapter() -> LocalBrokerAdapter:
    """The shipped baseline adapter. Future live-adapter tests
    parametrize this fixture to swap in their own concrete impl;
    the rest of the suite SHALL pass unchanged."""
    a = LocalBrokerAdapter(
        starting_cash=_eur("10000"),
        fee_model=FlatFeeModel(commission=_eur("0"), spread_bps=Decimal(0)),
        slippage_model=ZeroSlippageModel(),
    )
    a.register_instrument(_stock())
    return a


# ===========================================================================
# Protocol conformance
# ===========================================================================


def test_conformance_satisfies_broker_adapter_protocol(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_SDS_INT_001 / REQ_SDD_API_002 — the concrete adapter
    SHALL satisfy ``BrokerAdapter`` structurally via
    ``runtime_checkable`` Protocol."""
    assert isinstance(adapter, BrokerAdapter)


def test_conformance_every_protocol_method_exists(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — every method documented on the Protocol
    SHALL be reachable on the adapter."""
    for method in ("submit", "cancel", "positions", "account_state", "instrument", "subscribe"):
        assert callable(getattr(adapter, method)), (
            f"missing method {method}"
        )


# ===========================================================================
# submit
# ===========================================================================


def test_conformance_submit_returns_order_id_on_success(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — successful submit returns Ok(OrderId)."""
    adapter.process_tick(_tick(_stock()))
    result = adapter.submit(_order())
    assert isinstance(result, Ok)
    assert result.value == OrderId("o-1")


def test_conformance_submit_idempotent_on_duplicate_client_id(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_SDD_API_006 — re-submitting with the same client id
    returns the original Ok(OrderId)."""
    adapter.process_tick(_tick(_stock()))
    a = adapter.submit(_order(oid="o-dup"))
    b = adapter.submit(_order(oid="o-dup"))
    assert isinstance(a, Ok) and isinstance(b, Ok)
    assert a.value == b.value
    assert len(adapter.positions()) == 1
    assert adapter.positions()[0].quantity == Decimal("10")  # not 20


def test_conformance_submit_categorised_err_on_no_market_data(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — submit before any tick returns
    ``broker:no_market_data``."""
    # Note: fixture register_instrument doesn't push a tick.
    result = adapter.submit(_order())
    assert isinstance(result, Err)
    assert result.error.startswith("broker:no_market_data")


def test_conformance_submit_categorised_err_on_unsupported_type(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — LIMIT orders return
    ``broker:order_unsupported``."""
    adapter.process_tick(_tick(_stock()))
    limit_order = Order(
        id=OrderId("o-limit"),
        instrument=_stock(),
        side=Side.BUY,
        quantity=Decimal("10"),
        type=OrderType.LIMIT,
        stop_loss=StopLoss(price=Decimal("9")),
        created_at=_ts(1),
        source_strategy=StrategyId("test"),
        limit_price=Decimal("95"),
    )
    result = adapter.submit(limit_order)
    assert isinstance(result, Err)
    assert result.error.startswith("broker:order_unsupported")


# ===========================================================================
# cancel
# ===========================================================================


def test_conformance_cancel_filled_returns_already_filled(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — cancel after fill returns
    ``broker:already_filled``."""
    adapter.process_tick(_tick(_stock()))
    submit_result = adapter.submit(_order())
    assert isinstance(submit_result, Ok)
    cancel_result = adapter.cancel(submit_result.value)
    assert isinstance(cancel_result, Err)
    assert cancel_result.error.startswith("broker:already_filled")


def test_conformance_cancel_unknown_returns_not_found(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — cancelling an unknown order returns
    ``broker:not_found``."""
    result = adapter.cancel(OrderId("o-ghost"))
    assert isinstance(result, Err)
    assert result.error.startswith("broker:not_found")


# ===========================================================================
# positions
# ===========================================================================


def test_conformance_positions_empty_at_boot(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — positions() returns [] at boot."""
    assert adapter.positions() == []


def test_conformance_positions_reflect_filled_orders(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — positions() reflects every filled order;
    quantities accumulate per instrument."""
    adapter.process_tick(_tick(_stock()))
    adapter.submit(_order(oid="o-a"))
    adapter.submit(_order(oid="o-b", quantity=Decimal("5")))
    positions = adapter.positions()
    assert len(positions) == 1
    assert positions[0].quantity == Decimal("15")  # 10 + 5


# ===========================================================================
# account_state
# ===========================================================================


def test_conformance_account_state_starts_with_full_cash(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — at boot, ``account_state().cash`` ==
    starting_cash."""
    account = adapter.account_state()
    assert account.cash == _eur("10000")
    assert account.realized_pnl == _eur("0")
    assert account.unrealized_pnl == _eur("0")


def test_conformance_account_equity_identity_after_fill(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — after a fill, equity satisfies the
    documented identity (cash + cost_basis + unrealized_pnl).
    With ZeroSlippage and zero fees, the fill leaves cash =
    starting - price × qty; unrealized starts at 0 (mark equals
    entry); equity = cash + cost_basis."""
    adapter.process_tick(_tick(_stock(), last="100"))
    adapter.submit(_order())  # 10 units at ~100 = ~1000
    account = adapter.account_state()
    # Cash decreased by ~1000; unrealized = 0 (entry tick is the
    # mark); equity ~= 10000.
    assert account.cash.amount < Decimal("10000")
    assert account.equity.amount == account.cash.amount + (
        Decimal("10") * Decimal("100")
    )  # cost basis = qty × entry price


# ===========================================================================
# instrument
# ===========================================================================


def test_conformance_instrument_lookup_returns_some_on_registered(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — instrument() returns Some(Instrument) for
    a registered symbol."""
    result = adapter.instrument("ASML")
    assert isinstance(result, Some)
    assert result.value.symbol == "ASML"


def test_conformance_instrument_lookup_returns_nothing_for_unknown(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — instrument() returns Nothing for unknown
    symbols."""
    result = adapter.instrument("NOPE")
    assert isinstance(result, Nothing)


# ===========================================================================
# subscribe
# ===========================================================================


def test_conformance_subscribe_delivers_ticks_to_callback(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — subscribe() returns a Subscription;
    matching ticks invoke the callback."""
    received: list[Tick] = []
    sub = adapter.subscribe(["*"], received.append)
    assert isinstance(sub, Subscription)
    adapter.process_tick(_tick(_stock()))
    assert len(received) == 1


def test_conformance_subscribe_cancel_stops_callbacks(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_F_BRK_001 — Subscription.cancel() stops further
    callbacks; subsequent cancels are idempotent."""
    received: list[Tick] = []
    sub = adapter.subscribe(["*"], received.append)
    sub.cancel()
    adapter.process_tick(_tick(_stock()))
    assert received == []
    # Idempotent re-cancel.
    sub.cancel()
    adapter.process_tick(_tick(_stock(), last="101"))
    assert received == []


# ===========================================================================
# Full-lifecycle smoke
# ===========================================================================


def test_conformance_full_buy_sell_cycle(
    adapter: LocalBrokerAdapter,
) -> None:
    """REQ_TP_STR_004 — full happy-path lifecycle: register,
    tick, buy 10 units, tick higher, sell 10 units, verify
    realized P&L is positive."""
    stock = _stock()
    adapter.process_tick(_tick(stock, last="100"))
    buy_result = adapter.submit(_order(oid="o-buy", side=Side.BUY))
    assert isinstance(buy_result, Ok)
    # Move the market up.
    adapter.process_tick(_tick(stock, last="110"))
    sell_result = adapter.submit(_order(oid="o-sell", side=Side.SELL))
    assert isinstance(sell_result, Ok)
    account = adapter.account_state()
    # Realized P&L is positive (10 units × (110 - 100) = +100 EUR
    # under ZeroSlippage + zero fees).
    assert account.realized_pnl.amount > Decimal("0")
    # No open positions after the round-trip.
    assert adapter.positions() == []
