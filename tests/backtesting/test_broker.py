"""Tests for ``trading_system.backtesting.broker``.

The wrapper is thin; tests verify it surfaces the resulting Trade
and routes errors from the underlying adapter.

REQ refs: REQ_F_BRK_001..005, REQ_SDS_FLO_003.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.backtesting.broker import BacktestBroker
from trading_system.execution.fees import FeeModel, FlatFeeModel
from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.slippage import SlippageModel, ZeroSlippageModel
from trading_system.execution.types import Tick
from trading_system.models.identifiers import InstrumentId, OrderId, StrategyId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Order, OrderType, Side, StopLoss
from trading_system.result import Err, Nothing, Ok, Some

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


def _build() -> tuple[BacktestBroker, LocalBrokerAdapter, Stock]:
    s = _stock()
    fees: FeeModel = FlatFeeModel(commission=_eur("1.00"), spread_bps=Decimal(0))
    slip: SlippageModel = ZeroSlippageModel()
    adapter = LocalBrokerAdapter(
        starting_cash=_eur("10000"),
        fee_model=fees,
        slippage_model=slip,
        seed=1,
    )
    adapter.register_instrument(s)
    return BacktestBroker(adapter=adapter), adapter, s


def _tick(at: datetime, iid: InstrumentId, last: str) -> Tick:
    p = Decimal(last)
    return Tick(at=at, instrument_id=iid, bid=p, ask=p, last=p)


def _order(s: Stock, side: Side = Side.BUY, qty: str = "10", oid: str = "O1") -> Order:
    return Order(
        id=OrderId(oid),
        instrument=s,
        side=side,
        quantity=Decimal(qty),
        type=OrderType.MARKET,
        stop_loss=StopLoss(price=Decimal("40")),
        created_at=_ts(1),
        source_strategy=StrategyId("S1"),
    )


class TestProcessTick:
    def test_forwards_to_adapter(self) -> None:
        broker, _, s = _build()
        broker.process_tick(_tick(_ts(1), s.id, "50"))
        assert broker.latest_tick(s.id) == Some(_tick(_ts(1), s.id, "50"))

    def test_latest_tick_returns_nothing_when_unseen(self) -> None:
        broker, _, _ = _build()
        assert broker.latest_tick(InstrumentId("X")) == Nothing()


class TestSubmit:
    def test_returns_resulting_trade_on_success(self) -> None:
        broker, _, s = _build()
        broker.process_tick(_tick(_ts(1), s.id, "50"))
        match broker.submit(_order(s)):
            case Ok(trade):
                assert trade.price == Decimal("50")
                assert trade.quantity_filled == Decimal("10")
                assert trade.fees == _eur("1.00")
            case Err(e):
                raise AssertionError(f"unexpected Err: {e}")

    def test_propagates_adapter_error(self) -> None:
        broker, _, s = _build()
        # No tick processed → adapter rejects with broker:no_market_data.
        match broker.submit(_order(s)):
            case Ok(_):
                raise AssertionError("expected Err")
            case Err(reason):
                assert reason.startswith("broker:no_market_data")

    def test_duplicate_submit_emits_no_trade_err(self) -> None:
        broker, _, s = _build()
        broker.process_tick(_tick(_ts(1), s.id, "50"))
        first = broker.submit(_order(s, oid="DUP"))
        assert isinstance(first, Ok)
        # Resubmitting the same order id is idempotent at the adapter
        # (returns same OrderId, no new trade); the wrapper surfaces
        # this as Err so the engine catches the duplicate.
        match broker.submit(_order(s, oid="DUP")):
            case Err(reason):
                assert "no_trade_emitted" in reason
            case Ok(_):
                raise AssertionError("expected Err on duplicate submit")
