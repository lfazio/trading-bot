"""``NotifyingBrokerWrapper`` tests — CR-001 Phase B step 2.

REQ refs:
- REQ_F_NOT_007 — AnomalyAlert on broker rejection.
- REQ_SDD_NOT_006 — emitter lives at the boundary between the
  execution subsystem and the notification fan-out; the wrapped
  adapter stays untouched.
- REQ_NF_NOT_001 — emission is fire-and-forget; the wrapped
  submit/cancel keeps its synchronous return semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from trading_system.execution.types import Account as BrokerAccount, Tick
from trading_system.models.identifiers import AccountId, OrderId
from trading_system.models.instrument import Instrument
from trading_system.models.trading import Order, Position
from trading_system.notifications.broker_emitter import NotifyingBrokerWrapper
from trading_system.notifications.channels.local_log import (
    MemoryNotificationChannel,
)
from trading_system.notifications.emitters import AnomalyEmitter
from trading_system.notifications.fanout import NotificationFanOut
from trading_system.result import Err, Nothing, Ok, Option, Result


_ACCOUNT = AccountId("default")


# ---------------------------------------------------------------------------
# Stub broker adapter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _StubBroker:
    """Minimal BrokerAdapter Protocol implementation for the wrapper
    test. Returns parametrised Results so we exercise both Ok + Err
    paths through the wrapper."""

    submit_result: Result[OrderId, str] = field(
        default_factory=lambda: Ok(OrderId("o-1"))
    )
    cancel_result: Result[bool, str] = field(
        default_factory=lambda: Ok(True)
    )
    submit_calls: list[Order] = field(default_factory=list)
    cancel_calls: list[OrderId] = field(default_factory=list)

    def submit(self, order: Order) -> Result[OrderId, str]:
        self.submit_calls.append(order)
        return self.submit_result

    def cancel(self, order_id: OrderId) -> Result[bool, str]:
        self.cancel_calls.append(order_id)
        return self.cancel_result

    def positions(self) -> list[Position]:
        return []

    def account_state(self) -> BrokerAccount:
        from decimal import Decimal

        from trading_system.models.money import Currency, Money

        return BrokerAccount(
            cash=Money(Decimal("10000"), Currency.EUR),
            realized_pnl=Money(Decimal("0"), Currency.EUR),
            unrealized_pnl=Money(Decimal("0"), Currency.EUR),
            equity=Money(Decimal("10000"), Currency.EUR),
        )

    def instrument(self, symbol: str) -> Option[Instrument]:
        return Nothing()

    def subscribe(
        self, symbols: list[str], on_tick: Callable[[Tick], None]
    ) -> Any:
        return _StubSubscription()


@dataclass(slots=True)
class _StubSubscription:
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


def _emitter() -> tuple[AnomalyEmitter, MemoryNotificationChannel]:
    channel = MemoryNotificationChannel()
    fanout = NotificationFanOut(channels=(channel,))
    return AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT), channel


def _order() -> Order:
    """Minimal Order using the canonical constructor shape."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from trading_system.models.identifiers import InstrumentId, StrategyId
    from trading_system.models.instrument import InstrumentClass, Stock
    from trading_system.models.money import Currency
    from trading_system.models.trading import OrderType, Side, StopLoss

    stock = Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )
    return Order(
        id=OrderId("o-test-1"),
        instrument=stock,
        side=Side.BUY,
        type=OrderType.MARKET,
        quantity=Decimal("10"),
        stop_loss=StopLoss(price=Decimal("90")),
        created_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        source_strategy=StrategyId("test"),
    )


# ---------------------------------------------------------------------------
# Submit / cancel happy path — no emission
# ---------------------------------------------------------------------------


def test_submit_ok_does_not_emit() -> None:
    emitter, channel = _emitter()
    wrapped = NotifyingBrokerWrapper(inner=_StubBroker(), emitter=emitter)
    result = wrapped.submit(_order())
    assert isinstance(result, Ok)
    assert channel.delivered == []  # no AnomalyAlert on success


def test_cancel_ok_does_not_emit() -> None:
    emitter, channel = _emitter()
    wrapped = NotifyingBrokerWrapper(inner=_StubBroker(), emitter=emitter)
    wrapped.cancel(OrderId("o-x"))
    assert channel.delivered == []


# ---------------------------------------------------------------------------
# Submit / cancel Err — emission fires
# ---------------------------------------------------------------------------


def test_submit_err_emits_anomaly_alert() -> None:
    emitter, channel = _emitter()
    stub = _StubBroker(submit_result=Err("broker:no_market_data: ASML.AS"))
    wrapped = NotifyingBrokerWrapper(inner=stub, emitter=emitter)
    result = wrapped.submit(_order())
    assert isinstance(result, Err)
    assert len(channel.delivered) == 1
    payload = channel.delivered[0]
    assert payload.code == "broker:no_market_data: ASML.AS"
    assert "ASML.AS" in payload.message


def test_cancel_err_emits_anomaly_alert() -> None:
    emitter, channel = _emitter()
    stub = _StubBroker(cancel_result=Err("broker:already_filled: o-x"))
    wrapped = NotifyingBrokerWrapper(inner=stub, emitter=emitter)
    wrapped.cancel(OrderId("o-x"))
    assert len(channel.delivered) == 1
    payload = channel.delivered[0]
    assert payload.code == "broker:already_filled: o-x"
    assert "cancel o-x" in payload.message


# ---------------------------------------------------------------------------
# emitter=None ⇒ no emission ever; wrapper is transparent
# ---------------------------------------------------------------------------


def test_wrapper_with_none_emitter_is_transparent_on_submit_err() -> None:
    stub = _StubBroker(submit_result=Err("broker:rejected"))
    wrapped = NotifyingBrokerWrapper(inner=stub, emitter=None)
    result = wrapped.submit(_order())
    assert isinstance(result, Err)
    # No fan-out wired; no assertion about emission — just that the
    # call didn't blow up.


# ---------------------------------------------------------------------------
# Delegation of non-mutating methods
# ---------------------------------------------------------------------------


def test_positions_delegates_to_inner() -> None:
    emitter, _ = _emitter()
    wrapped = NotifyingBrokerWrapper(inner=_StubBroker(), emitter=emitter)
    assert wrapped.positions() == []


def test_instrument_delegates_to_inner() -> None:
    emitter, _ = _emitter()
    wrapped = NotifyingBrokerWrapper(inner=_StubBroker(), emitter=emitter)
    assert wrapped.instrument("ASML.AS").is_none()


def test_subscribe_delegates_to_inner() -> None:
    emitter, _ = _emitter()
    wrapped = NotifyingBrokerWrapper(inner=_StubBroker(), emitter=emitter)
    sub = wrapped.subscribe(["ASML.AS"], lambda tick: None)
    assert hasattr(sub, "cancel")
