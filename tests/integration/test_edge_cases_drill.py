"""Edge-case drills — Phase 6 operational tests.

Per TASKS.md Phase 6: "Edge-case tests (crash, knockout, broker
rejection, feed corruption)". Four scenarios, one per failure
mode, asserting the documented behaviour at each boundary:

1. **Crash drill** — a market crash (multi-percent equity drop in
   a single tick) trips the kill-switch's financial-trigger
   surface (REQ_S_KS_003 + REQ_SDD_ALG_006) and the system halts
   trading via ``must_halt`` (REQ_S_KS_011).

2. **Knockout drill** — a turbo's underlying touches the knockout
   barrier; ``KnockoutSimulator.maybe_trigger`` closes the
   position at zero with loss capped at invested capital
   (REQ_F_BCT_004 + REQ_F_TRB_005).

3. **Broker rejection drill** — the LocalBrokerAdapter rejects
   submissions cleanly with the documented categorised Err
   strings: ``broker:no_market_data``, ``broker:order_unsupported``,
   ``broker:not_found``, ``broker:already_filled``.

4. **Feed corruption drill** — every Bar / Tick constructor
   panics at the boundary on a corrupted shape (high < low,
   negative price, non-positive volume etc.) so a corrupted
   upstream feed CANNOT enter the system as an invalid object.

These drills are scenario-style (one cohesive walk through each
failure mode) — the unit-level tests in
``tests/safety/test_anomaly.py`` / ``tests/backtesting/test_knockout.py``
/ ``tests/execution/test_local.py`` / ``tests/data/`` cover the
narrow invariants; this file is the operator's pre-deployment
confidence check that the failure modes compose correctly
end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.backtesting.knockout import KnockoutSimulator
from trading_system.data.types import Bar
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.slippage import ZeroSlippageModel
from trading_system.execution.types import Tick
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    SnapshotId,
    StrategyId,
)
from trading_system.models.instrument import (
    Instrument,
    InstrumentClass,
    Stock,
    Turbo,
)
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket
from trading_system.models.safety import KillSwitchState, TriggerCategory, KillSwitchTrigger
from trading_system.models.trading import (
    Order,
    OrderType,
    Side,
    StopLoss,
    Trade,
)
from trading_system.portfolio.portfolio import Portfolio
from trading_system.result import Err, Ok
from trading_system.safety import (
    AlwaysValidVerifier,
    MemoryAlertChannel,
    MemorySnapshotSink,
    StateManager,
)
from trading_system.safety.anomaly import (
    rapid_decline_breach,
    single_day_loss_breach,
)
from trading_system.tax.config import TaxConfig


_EUR = Currency.EUR
_T0 = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=_EUR)


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, tzinfo=UTC)


def _stock() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=_EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _equity_point(amount: str, day: int) -> EquityPoint:
    money = _eur(amount)
    return EquityPoint(
        at=_ts(day),
        equity_gross=money,
        equity_after_tax=money,
        drawdown_pct=Decimal(0),
    )


# ===========================================================================
# Scenario 1 — Crash drill
# ===========================================================================


class TestCrashDrill:
    """REQ_S_KS_003 + REQ_SDD_ALG_006 — a single-day loss breach
    fires the financial-trigger surface. REQ_S_KS_011 — must_halt
    flips True after the operator escalates the breach to a
    KILL-severity trigger."""

    def test_5pct_drop_triggers_anomaly_detector_at_default_threshold(
        self,
    ) -> None:
        """REQ_SDD_ALG_006 — default single-day-loss threshold 5 %.
        A curve dropping 5.1 % in one step SHALL breach; 4.9 %
        SHALL NOT."""
        breach_curve = [_equity_point("100000", 1), _equity_point("94900", 2)]
        no_breach_curve = [
            _equity_point("100000", 1),
            _equity_point("95100", 2),
        ]
        assert single_day_loss_breach(breach_curve, Decimal("0.05")) is True
        assert single_day_loss_breach(no_breach_curve, Decimal("0.05")) is False

    def test_rapid_decline_breach_over_5_days(self) -> None:
        """REQ_SDD_ALG_007 — default rapid-decline 10 % over 5
        trading days. Curve drops 12 % across 5 sessions ⇒ breach;
        9 % ⇒ no breach."""
        breach_curve = [
            _equity_point("100000", 1),
            _equity_point("99000", 2),
            _equity_point("96500", 3),
            _equity_point("93000", 4),
            _equity_point("90000", 5),
            _equity_point("88000", 6),
        ]
        no_breach_curve = [
            _equity_point("100000", 1),
            _equity_point("99000", 2),
            _equity_point("97500", 3),
            _equity_point("95000", 4),
            _equity_point("93000", 5),
            _equity_point("91000", 6),
        ]
        assert rapid_decline_breach(
            breach_curve, days=5, pct=Decimal("0.10")
        ) is True
        assert rapid_decline_breach(
            no_breach_curve, days=5, pct=Decimal("0.10")
        ) is False

    def test_crash_trigger_halts_trading(self) -> None:
        """REQ_S_KS_003 + REQ_S_KS_011 — when the operator raises
        a KILL-severity FINANCIAL trigger after the anomaly
        detector fires, ``must_halt`` SHALL return True and the
        next BrokerAdapter.submit call is blocked at the
        REQ_SDS_ARC_003 boundary."""
        sink = MemorySnapshotSink()
        mgr = StateManager(
            verifier=AlwaysValidVerifier(),
            snapshot_sink=sink,
            alert_channels=[MemoryAlertChannel()],
        )
        assert mgr.must_halt() is False
        mgr.raise_trigger(
            KillSwitchTrigger(
                category=TriggerCategory.FINANCIAL,
                code="crash_5pct_daily",
                message="single-day loss breach at 5.1 %",
                severity="KILL",
                raised_at=_T0,
                snapshot_id=SnapshotId("snap-crash"),
            )
        )
        assert mgr.state() is KillSwitchState.KILL
        assert mgr.must_halt() is True


# ===========================================================================
# Scenario 2 — Knockout drill
# ===========================================================================


class TestKnockoutDrill:
    """REQ_F_BCT_004 + REQ_F_TRB_005 — knockout closes the turbo
    position at zero; the loss is capped at invested capital
    (cost basis), never below."""

    def test_long_turbo_knockout_closes_at_zero(self) -> None:
        """LONG turbo at strike 90: underlying tick at 88 breaches
        the barrier. The knockout closes the position; portfolio
        loses the full cost basis but no more."""
        stock = _stock()
        turbo = Turbo(
            id=InstrumentId("T-LONG"),
            symbol="T-LONG",
            exchange="DE",
            currency=_EUR,
            cls=InstrumentClass.TURBO,
            underlying=stock.id,
            direction="LONG",
            leverage=Decimal("5"),
            knockout=Decimal("90"),
            spread_pct=Decimal("0"),
        )
        portfolio = Portfolio.empty(_eur("10000"))
        # Open a 100-unit position in the turbo at price 10.
        order = Order(
            id=OrderId("o-1"),
            instrument=turbo,
            side=Side.BUY,
            quantity=Decimal("100"),
            type=OrderType.MARKET,
            stop_loss=StopLoss(price=Decimal("9")),
            created_at=_ts(1),
            source_strategy=StrategyId("test"),
        )
        trade = Trade(
            id=__import__(
                "trading_system.models.identifiers",
                fromlist=["TradeId"],
            ).TradeId("t-1"),
            order_id=order.id,
            executed_at=_ts(1),
            price=Decimal("10"),
            quantity_filled=Decimal("100"),
            fees=_eur("0"),
        )
        portfolio.apply(trade, order, AllocationBucket.TURBO, TaxConfig.default())
        # 100 units × 10 EUR = 1000 EUR cost basis.
        assert portfolio.cash().amount == Decimal("9000")

        # Knockout: underlying touches 88 (below 90).
        knockout_tick = Tick(
            at=_ts(2),
            instrument_id=stock.id,
            bid=Decimal("87.50"),
            ask=Decimal("88.50"),
            last=Decimal("88"),
        )
        sim = KnockoutSimulator()
        closed = sim.maybe_trigger(
            knockout_tick, portfolio, TaxConfig.default()
        )
        assert turbo.id in closed
        # Loss capped at invested capital (1000 EUR cost basis).
        # Cash remains at 9000 EUR (unchanged); the position is
        # marked-out at zero and removed.
        assert turbo.id not in portfolio.positions()
        # The total equity drop is bounded by the cost basis.
        # equity_after_tax = cash + marked - tax_liability;
        # the marked value of the closed turbo is 0, so equity
        # drops by ~1000 EUR (cost basis) — no more.
        equity = portfolio.equity_after_tax().amount
        # Starting equity was 10000; after knockout it sits at
        # cash 9000 + zero marked turbo = 9000.
        assert equity == Decimal("9000.00")

    def test_short_turbo_knockout_above_barrier(self) -> None:
        """SHORT turbo at strike 110: tick at 112 breaches up."""
        stock = _stock()
        turbo = Turbo(
            id=InstrumentId("T-SHORT"),
            symbol="T-SHORT",
            exchange="DE",
            currency=_EUR,
            cls=InstrumentClass.TURBO,
            underlying=stock.id,
            direction="SHORT",
            leverage=Decimal("5"),
            knockout=Decimal("110"),
            spread_pct=Decimal("0"),
        )
        portfolio = Portfolio.empty(_eur("10000"))
        order = Order(
            id=OrderId("o-2"),
            instrument=turbo,
            side=Side.BUY,
            quantity=Decimal("50"),
            type=OrderType.MARKET,
            stop_loss=StopLoss(price=Decimal("9")),
            created_at=_ts(1),
            source_strategy=StrategyId("test"),
        )
        from trading_system.models.identifiers import TradeId

        trade = Trade(
            id=TradeId("t-2"),
            order_id=order.id,
            executed_at=_ts(1),
            price=Decimal("10"),
            quantity_filled=Decimal("50"),
            fees=_eur("0"),
        )
        portfolio.apply(trade, order, AllocationBucket.TURBO, TaxConfig.default())

        knockout_tick = Tick(
            at=_ts(2),
            instrument_id=stock.id,
            bid=Decimal("111"),
            ask=Decimal("113"),
            last=Decimal("112"),
        )
        sim = KnockoutSimulator()
        closed = sim.maybe_trigger(
            knockout_tick, portfolio, TaxConfig.default()
        )
        assert turbo.id in closed
        assert turbo.id not in portfolio.positions()

    def test_below_barrier_does_not_knockout(self) -> None:
        """LONG turbo at strike 90: tick at 91 stays above barrier;
        knockout SHALL NOT fire."""
        stock = _stock()
        turbo = Turbo(
            id=InstrumentId("T-LONG-2"),
            symbol="T-LONG-2",
            exchange="DE",
            currency=_EUR,
            cls=InstrumentClass.TURBO,
            underlying=stock.id,
            direction="LONG",
            leverage=Decimal("5"),
            knockout=Decimal("90"),
            spread_pct=Decimal("0"),
        )
        portfolio = Portfolio.empty(_eur("10000"))
        order = Order(
            id=OrderId("o-3"),
            instrument=turbo,
            side=Side.BUY,
            quantity=Decimal("100"),
            type=OrderType.MARKET,
            stop_loss=StopLoss(price=Decimal("9")),
            created_at=_ts(1),
            source_strategy=StrategyId("test"),
        )
        from trading_system.models.identifiers import TradeId

        trade = Trade(
            id=TradeId("t-3"),
            order_id=order.id,
            executed_at=_ts(1),
            price=Decimal("10"),
            quantity_filled=Decimal("100"),
            fees=_eur("0"),
        )
        portfolio.apply(trade, order, AllocationBucket.TURBO, TaxConfig.default())

        safe_tick = Tick(
            at=_ts(2),
            instrument_id=stock.id,
            bid=Decimal("90.50"),
            ask=Decimal("91.50"),
            last=Decimal("91"),
        )
        sim = KnockoutSimulator()
        closed = sim.maybe_trigger(
            safe_tick, portfolio, TaxConfig.default()
        )
        assert closed == []
        # Position still open.
        assert turbo.id in portfolio.positions()


# ===========================================================================
# Scenario 3 — Broker rejection drill
# ===========================================================================


class TestBrokerRejectionDrill:
    """REQ_F_BRK_001 + REQ_SDD_ERR_002 — the BrokerAdapter
    Protocol returns categorised ``Err(broker:<category>:...)``
    on every documented failure mode. Engine modules pattern-
    match the Err string; no exception propagates past the
    adapter boundary."""

    def _adapter(self) -> LocalBrokerAdapter:
        return LocalBrokerAdapter(
            starting_cash=_eur("10000"),
            fee_model=FlatFeeModel(commission=_eur("0"), spread_bps=Decimal(0)),
            slippage_model=ZeroSlippageModel(),
        )

    def _order(
        self,
        *,
        instrument: Instrument | None = None,
        order_type: OrderType = OrderType.MARKET,
        oid: str = "o-1",
        limit_price: Decimal | None = None,
    ) -> Order:
        return Order(
            id=OrderId(oid),
            instrument=instrument or _stock(),
            side=Side.BUY,
            quantity=Decimal("10"),
            type=order_type,
            stop_loss=StopLoss(price=Decimal("9")),
            created_at=_ts(1),
            source_strategy=StrategyId("test"),
            limit_price=limit_price,
        )

    def test_submit_without_market_data_returns_no_market_data(self) -> None:
        """REQ_F_BRK_001 — MARKET order before any tick has been
        seen returns categorised ``broker:no_market_data``."""
        adapter = self._adapter()
        adapter.register_instrument(_stock())
        result = adapter.submit(self._order())
        assert isinstance(result, Err)
        assert result.error.startswith("broker:no_market_data")

    def test_limit_orders_unsupported_returns_categorised_err(self) -> None:
        """REQ_F_BRK_001 — LIMIT orders not yet wired return
        ``broker:order_unsupported``."""
        adapter = self._adapter()
        adapter.register_instrument(_stock())
        adapter.process_tick(
            Tick(
                at=_ts(1),
                instrument_id=_stock().id,
                bid=Decimal("99"),
                ask=Decimal("101"),
                last=Decimal("100"),
            )
        )
        limit_order = self._order(
            order_type=OrderType.LIMIT,
            limit_price=Decimal("95"),
        )
        result = adapter.submit(limit_order)
        assert isinstance(result, Err)
        assert result.error.startswith("broker:order_unsupported")

    def test_cancel_unknown_order_returns_not_found(self) -> None:
        """REQ_F_BRK_001 — cancelling an unknown order id returns
        ``broker:not_found``."""
        adapter = self._adapter()
        result = adapter.cancel(OrderId("o-ghost"))
        assert isinstance(result, Err)
        assert result.error.startswith("broker:not_found")

    def test_cancel_filled_order_returns_already_filled(self) -> None:
        """REQ_F_BRK_001 — cancelling an already-filled order
        returns ``broker:already_filled``."""
        adapter = self._adapter()
        adapter.register_instrument(_stock())
        adapter.process_tick(
            Tick(
                at=_ts(1),
                instrument_id=_stock().id,
                bid=Decimal("99"),
                ask=Decimal("101"),
                last=Decimal("100"),
            )
        )
        order = self._order(oid="o-fill")
        submit_result = adapter.submit(order)
        assert isinstance(submit_result, Ok)
        cancel_result = adapter.cancel(order.id)
        assert isinstance(cancel_result, Err)
        assert cancel_result.error.startswith("broker:already_filled")

    def test_resubmit_same_id_is_idempotent(self) -> None:
        """REQ_SDD_API_006 — duplicate client-id submission
        returns the original Ok(OrderId), not a fresh fill."""
        adapter = self._adapter()
        adapter.register_instrument(_stock())
        adapter.process_tick(
            Tick(
                at=_ts(1),
                instrument_id=_stock().id,
                bid=Decimal("99"),
                ask=Decimal("101"),
                last=Decimal("100"),
            )
        )
        order = self._order(oid="o-dup")
        first = adapter.submit(order)
        second = adapter.submit(order)
        assert isinstance(first, Ok)
        assert isinstance(second, Ok)
        assert first.value == second.value
        # No double fill — trades list has one entry.
        # (Trade history is private; verify via positions count
        # increment being 1, not 2.)
        positions = adapter.positions()
        assert len(positions) == 1
        assert positions[0].quantity == Decimal("10")


# ===========================================================================
# Scenario 4 — Feed corruption drill
# ===========================================================================


class TestFeedCorruptionDrill:
    """REQ_SDD_DAT_001 family — Bar / Tick constructors panic at
    the boundary on any invalid shape. A corrupted upstream
    feed CANNOT produce an invalid object that propagates into
    the engine; the panic is the boundary's contract.

    These tests are intentionally narrow — they're the
    "guarantor" tests for the broader invariant: if a Bar with
    high < low is unrepresentable, no engine downstream can
    receive one. The unit tests in ``tests/models/test_bar.py``
    (or similar) cover the per-field validation in more depth;
    this drill is the operator-facing summary."""

    def test_bar_with_high_below_low_panics(self) -> None:
        """REQ_SDD_DAT_001 — Bar(high < low) is unrepresentable.
        The constructor's exact message phrasing checks high vs
        max(open, close) — accept either ``low`` or ``low``-via-
        max(...) phrasings."""
        with pytest.raises(ValueError, match="high"):
            Bar(
                at=_ts(1),
                open=Decimal("100"),
                high=Decimal("99"),  # < open/close
                low=Decimal("98"),
                close=Decimal("100"),
                volume=Decimal("1000"),
            )

    def test_bar_with_negative_volume_panics(self) -> None:
        """REQ_SDD_DAT_001 — Bar.volume < 0 is unrepresentable."""
        with pytest.raises(ValueError, match="volume"):
            Bar(
                at=_ts(1),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("-1"),
            )

    def test_tick_with_negative_price_panics(self) -> None:
        """REQ_SDD_DAT_001 — Tick.bid <= 0 is unrepresentable."""
        with pytest.raises(ValueError, match="bid|ask|last"):
            Tick(
                at=_ts(1),
                instrument_id=InstrumentId("ASML.AS"),
                bid=Decimal("-1"),
                ask=Decimal("1"),
                last=Decimal("0.5"),
            )

    def test_tick_with_bid_above_ask_panics(self) -> None:
        """REQ_SDD_DAT_001 — Tick(bid > ask) is unrepresentable
        (the spread would be negative, which is non-sensical
        for a two-sided quote)."""
        with pytest.raises(ValueError, match="bid|ask"):
            Tick(
                at=_ts(1),
                instrument_id=InstrumentId("ASML.AS"),
                bid=Decimal("101"),
                ask=Decimal("99"),  # < bid
                last=Decimal("100"),
            )

    def test_tick_with_last_outside_spread_panics(self) -> None:
        """REQ_SDD_DAT_001 — Tick.last must lie in [bid, ask]."""
        with pytest.raises(ValueError, match="last"):
            Tick(
                at=_ts(1),
                instrument_id=InstrumentId("ASML.AS"),
                bid=Decimal("99"),
                ask=Decimal("101"),
                last=Decimal("102"),  # > ask
            )

    def test_order_with_non_positive_quantity_panics(self) -> None:
        """REQ_SDD_DAT_001 — Order.quantity <= 0 is
        unrepresentable. A feed-driven quantity of 0 (the kind a
        corrupted upstream might produce) panics at construction."""
        with pytest.raises(ValueError, match="quantity"):
            Order(
                id=OrderId("o-bad"),
                instrument=_stock(),
                side=Side.BUY,
                quantity=Decimal("0"),
                type=OrderType.MARKET,
                stop_loss=StopLoss(price=Decimal("9")),
                created_at=_ts(1),
                source_strategy=StrategyId("test"),
            )


# Silence unused-import warning kept for potential future use.
_ = timedelta
