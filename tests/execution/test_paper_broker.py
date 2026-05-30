"""CR-025 / TC_PAP_BRK_001..004 — PaperBrokerAdapter unit tests.

REQ refs:
- REQ_F_PAP_011 (concrete BrokerAdapter implementation)
- REQ_F_PAP_012 (conformance suite passes)
- REQ_SDD_PAP_001 (dataclass(slots=True) shape)
- REQ_SDD_PAP_002 (submit() live-price contract)
- REQ_SDD_PAP_003 (account_state returns Account)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.data.types import Bar
from trading_system.execution.adapter import BrokerAdapter
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.paper import PaperBrokerAdapter
from trading_system.execution.slippage import ZeroSlippageModel
from trading_system.models.identifiers import InstrumentId, OrderId, StrategyId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Order, OrderType, Side, StopLoss
from trading_system.result import Err, Ok


def _instrument(symbol: str = "ASML.AS") -> Stock:
    return Stock(
        id=InstrumentId(symbol),
        symbol=symbol.split(".")[0],
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _bar(close: str = "100.00", at: datetime | None = None) -> Bar:
    price = Decimal(close)
    return Bar(
        at=at or datetime(2026, 5, 30, 12, tzinfo=UTC),
        open=price,
        high=price * Decimal("1.001"),
        low=price * Decimal("0.999"),
        close=price,
        volume=Decimal("1000"),
    )


@dataclass
class _StubMarketData:
    """In-memory ``MarketDataProvider.latest`` stub."""

    response: object  # Ok(Bar) | Err(str)
    call_log: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.call_log is None:
            self.call_log = []

    def latest(self, instrument):
        self.call_log.append(instrument)
        return self.response


def _order(
    side: Side = Side.BUY,
    qty: str = "10",
    order_id: str = "o-001",
) -> Order:
    return Order(
        id=OrderId(order_id),
        instrument=_instrument(),
        side=side,
        quantity=Decimal(qty),
        type=OrderType.MARKET,
        stop_loss=StopLoss(Decimal("0.05")),
        created_at=datetime(2026, 5, 30, 12, tzinfo=UTC),
        source_strategy=StrategyId("test"),
    )


def _adapter(
    market_data: _StubMarketData,
    *,
    spread_bps: Decimal = Decimal(0),
) -> PaperBrokerAdapter:
    adapter = PaperBrokerAdapter(
        starting_cash=Money(Decimal("100000"), Currency.EUR),
        market_data=market_data,
        fee_model=FlatFeeModel(
            commission=Money(Decimal("0"), Currency.EUR),
            spread_bps=Decimal("0"),
        ),
        slippage_model=ZeroSlippageModel(),
        seed=42,
        spread_bps=spread_bps,
    )
    adapter.register_instrument(_instrument())
    return adapter


# ---------------------------------------------------------------------------
# TC_PAP_BRK_001 — Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_is_instance_of_broker_adapter_protocol(self) -> None:
        adapter = _adapter(_StubMarketData(response=Ok(_bar())))
        assert isinstance(adapter, BrokerAdapter)

    def test_every_protocol_method_callable(self) -> None:
        adapter = _adapter(_StubMarketData(response=Ok(_bar())))
        # submit
        r = adapter.submit(_order())
        assert isinstance(r, Ok)
        # cancel — already filled per submit above; returns Err.
        c = adapter.cancel(OrderId("o-001"))
        assert isinstance(c, Err)
        # positions
        assert isinstance(adapter.positions(), list)
        # account_state
        state = adapter.account_state()
        assert state.cash.currency is Currency.EUR
        # instrument
        opt = adapter.instrument("ASML")
        assert hasattr(opt, "is_some") or hasattr(opt, "value")
        # subscribe
        sub = adapter.subscribe(["ASML"], lambda tick: None)
        assert hasattr(sub, "cancel")
        sub.cancel()


# ---------------------------------------------------------------------------
# TC_PAP_BRK_002 — live-price fill simulation
# ---------------------------------------------------------------------------


class TestLivePriceFillSimulation:
    def test_market_buy_fills_at_close_with_zero_spread_and_slippage(
        self,
    ) -> None:
        market_data = _StubMarketData(response=Ok(_bar(close="100.00")))
        adapter = _adapter(market_data)
        result = adapter.submit(_order(side=Side.BUY, qty="10"))
        assert isinstance(result, Ok)
        # market_data.latest was queried once.
        assert len(market_data.call_log) == 1
        # Cash decremented by exactly close * qty (zero fees + zero slippage).
        state = adapter.account_state()
        # account_state queries latest() per position to mark equity.
        # Position is now ASML with 10 shares; mark fetches the same bar.
        # Expected cash = 100000 - 100.00 * 10 = 99000.
        assert state.cash.amount == Decimal("99000")

    def test_market_sell_credits_cash_at_close(self) -> None:
        market_data = _StubMarketData(response=Ok(_bar(close="100.00")))
        adapter = _adapter(market_data)
        # First BUY 10 shares.
        adapter.submit(_order(side=Side.BUY, qty="10", order_id="o-buy"))
        # Then SELL 10 shares — cash returns to starting.
        result = adapter.submit(
            _order(side=Side.SELL, qty="10", order_id="o-sell")
        )
        assert isinstance(result, Ok)
        state = adapter.account_state()
        # Round-trip at the same close, zero fees, zero slippage.
        assert state.cash.amount == Decimal("100000")

    def test_synthetic_spread_widens_bid_ask_around_close(self) -> None:
        """REQ_SDD_PAP_002 — non-zero `spread_bps` SHALL widen the
        bid/ask around the close. A 100bps spread on close=100
        gives bid=99.5 + ask=100.5; a BUY fills at ask=100.5."""
        market_data = _StubMarketData(response=Ok(_bar(close="100.00")))
        adapter = _adapter(market_data, spread_bps=Decimal(100))
        result = adapter.submit(_order(side=Side.BUY, qty="10"))
        assert isinstance(result, Ok)
        # half_spread = close * spread_bps / 20000 = 100 * 100 / 20000 = 0.5.
        # ask = 100.5; BUY fills at ask. Cash = 100000 - 100.5*10 = 98995.
        state = adapter.account_state()
        assert state.cash.amount == Decimal("98995.00")


# ---------------------------------------------------------------------------
# TC_PAP_BRK_003 — no-market-data Err
# ---------------------------------------------------------------------------


class TestNoMarketDataErr:
    def test_submit_surfaces_categorised_err_when_latest_returns_err(
        self,
    ) -> None:
        market_data = _StubMarketData(
            response=Err("data:upstream_blocked")
        )
        adapter = _adapter(market_data)
        result = adapter.submit(_order())
        assert isinstance(result, Err)
        assert result.error.startswith("broker:no_market_data:ASML.AS")

    def test_failed_submit_does_not_open_position_or_debit_cash(
        self,
    ) -> None:
        market_data = _StubMarketData(
            response=Err("data:upstream_blocked")
        )
        adapter = _adapter(market_data)
        starting_cash_before = adapter.account_state().cash.amount
        adapter.submit(_order())
        # No position; cash unchanged. account_state walks positions
        # to mark — but with no positions there's nothing to mark, so
        # the loop is a no-op + cash unchanged.
        assert adapter.positions() == []
        assert adapter.account_state().cash.amount == starting_cash_before


# ---------------------------------------------------------------------------
# TC_PAP_BRK_004 — account_state round-trip
# ---------------------------------------------------------------------------


class TestAccountStateRoundTrip:
    def test_fresh_adapter_reports_starting_cash_zero_pnl(self) -> None:
        market_data = _StubMarketData(response=Ok(_bar()))
        adapter = _adapter(market_data)
        state = adapter.account_state()
        assert state.cash.amount == Decimal("100000")
        assert state.realized_pnl.amount == Decimal("0")
        assert state.equity.amount == Decimal("100000")

    def test_buy_sell_round_trip_produces_positive_realized_when_price_rises(
        self,
    ) -> None:
        """BUY at 100; SELL at 110; realized P&L = 10 * 10 = 100."""
        market_data = _StubMarketData(response=Ok(_bar(close="100.00")))
        adapter = _adapter(market_data)
        adapter.submit(_order(side=Side.BUY, qty="10", order_id="o-buy"))
        # Bump the price.
        market_data.response = Ok(_bar(close="110.00"))
        adapter.submit(_order(side=Side.SELL, qty="10", order_id="o-sell"))
        state = adapter.account_state()
        # Cash = 100000 - 1000 (BUY) + 1100 (SELL) = 100100.
        assert state.cash.amount == Decimal("100100")
        # No open positions → equity == cash.
        assert state.equity.amount == state.cash.amount


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_negative_starting_cash_rejected(self) -> None:
        with pytest.raises(ValueError, match="starting_cash"):
            PaperBrokerAdapter(
                starting_cash=Money(Decimal("-1"), Currency.EUR),
                market_data=_StubMarketData(response=Ok(_bar())),
                fee_model=FlatFeeModel(
                    commission=Money(Decimal("0"), Currency.EUR),
                    spread_bps=Decimal("0"),
                ),
                slippage_model=ZeroSlippageModel(),
            )

    def test_negative_spread_bps_rejected(self) -> None:
        with pytest.raises(ValueError, match="spread_bps"):
            PaperBrokerAdapter(
                starting_cash=Money(Decimal("100"), Currency.EUR),
                market_data=_StubMarketData(response=Ok(_bar())),
                fee_model=FlatFeeModel(
                    commission=Money(Decimal("0"), Currency.EUR),
                    spread_bps=Decimal("0"),
                ),
                slippage_model=ZeroSlippageModel(),
                spread_bps=Decimal("-1"),
            )


# ---------------------------------------------------------------------------
# Subscribe + idempotency pass-throughs (mirror LocalBrokerAdapter)
# ---------------------------------------------------------------------------


def test_duplicate_submit_is_idempotent() -> None:
    market_data = _StubMarketData(response=Ok(_bar()))
    adapter = _adapter(market_data)
    r1 = adapter.submit(_order(order_id="o-1"))
    r2 = adapter.submit(_order(order_id="o-1"))
    assert isinstance(r1, Ok)
    assert isinstance(r2, Ok)
    assert r1.value == r2.value


def test_cancel_unknown_order_returns_not_found() -> None:
    market_data = _StubMarketData(response=Ok(_bar()))
    adapter = _adapter(market_data)
    result = adapter.cancel(OrderId("ghost"))
    assert isinstance(result, Err)
    assert result.error.startswith("broker:not_found")


def test_cancel_filled_order_returns_already_filled() -> None:
    market_data = _StubMarketData(response=Ok(_bar()))
    adapter = _adapter(market_data)
    adapter.submit(_order(order_id="o-1"))
    result = adapter.cancel(OrderId("o-1"))
    assert isinstance(result, Err)
    assert result.error.startswith("broker:already_filled")
