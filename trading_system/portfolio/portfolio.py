"""``Portfolio`` — cash, positions, realized PnL, dividends, equity curve.

The class satisfies the ``PortfolioView`` Protocol (read-only surface
consumed by strategies and the risk engine) while providing mutating
``apply`` / ``apply_dividend`` / ``mark`` methods used by the backtest
engine and live execution layer.

Single-currency by construction: every Money flowing into the portfolio
MUST share ``starting_capital.currency``. Multi-currency portfolios are
deferred to the phase-5 currency hedger (``wealth_ops/``).

REQ refs:
- REQ_F_PRT_001 — ``equity_after_tax()`` is the canonical performance
  reference (cash + marked positions + realized after-tax + dividends
  after-tax).
- REQ_F_PRT_003 — ``exposure_pct(bucket)`` returns the share of equity
  allocated to ``bucket``. Buckets without positions return zero.
- REQ_F_BCT_006 — tax is applied at every realization; gross + net are
  tracked separately (and agree per ``net = gross x (1 - rate)``,
  rounded HALF-UP to cents).
- REQ_F_CAP_014 / REQ_SDD_DAT_001 — stop-loss is mandatory; carried on
  every Position.
- REQ_SDD_DAT_005 — ``Trade.fees`` is the executed fee, never an
  estimate; ``apply`` consumes it directly.
- REQ_SDS_MOD_011 — Portfolio is the integration point between the
  execution layer and the analytics / risk layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import InstrumentId, StrategyId
from trading_system.models.instrument import (
    InstrumentClass,
    StructuredProduct,
    Turbo,
)
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket
from trading_system.models.trading import Order, Position, Side, Trade
from trading_system.result import Nothing, Option, Some
from trading_system.tax.config import TaxConfig
from trading_system.tax.engine import net_dividend, net_gain


@dataclass(frozen=True, slots=True)
class RealizationEvent:
    """A single realization (close / partial close / direction-flip).

    Persisted on the Portfolio so ``attribution()`` (REQ_F_PRT_002 —
    Phase 6) can aggregate by strategy or by class without re-walking
    the trade log.
    """

    at: datetime
    strategy: StrategyId
    instrument_class: InstrumentClass
    realized_gross: Money
    realized_after_tax: Money


@dataclass(frozen=True, slots=True)
class DividendEvent:
    """A single dividend credit attributed to the strategy that
    opened the underlying position."""

    at: datetime
    strategy: StrategyId
    instrument_class: InstrumentClass
    gross: Money
    after_tax: Money


@dataclass(frozen=True, slots=True)
class AttributionRow:
    """One row of the Phase-6 attribution table (REQ_F_PRT_002).

    ``kind`` is ``"strategy"``, ``"class"``, or ``"nav"``. ``label``
    carries the human-readable id (strategy id for ``"strategy"``,
    instrument-class value for ``"class"``, ``"NAV"`` for the
    summary row).
    """

    kind: str
    label: str
    realized_gross: Money
    realized_after_tax: Money
    dividends_gross: Money
    dividends_after_tax: Money
    nav_after_tax: Money | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("strategy", "class", "nav"):
            raise ValueError(f"AttributionRow.kind must be strategy|class|nav, got {self.kind!r}")
        if self.kind == "nav" and self.nav_after_tax is None:
            raise ValueError("AttributionRow.nav_after_tax must be set for kind='nav'")
        if self.kind != "nav" and self.nav_after_tax is not None:
            raise ValueError("AttributionRow.nav_after_tax may only be set for kind='nav'")


@dataclass(slots=True)
class Portfolio:
    """In-memory portfolio state.

    Construct via ``Portfolio.empty(starting_capital)``; mutate via
    ``apply`` (a fill), ``apply_dividend`` (a cash dividend), ``mark``
    (refresh prices for marking-to-market), and ``record_equity``
    (append to ``equity_curve``).

    The class is intentionally minimal at this stage: it covers what
    the backtester needs (Phase 5 step 10 prerequisite). Phase-6
    attribution (REQ_F_PRT_002) and the dashboard hooks land in
    step 11 / 12.
    """

    _cash: Money
    _realized_gross: Money
    _realized_after_tax: Money
    _dividends_gross: Money
    _dividends_after_tax: Money
    _positions: dict[InstrumentId, Position] = field(default_factory=dict)
    _position_buckets: dict[InstrumentId, AllocationBucket] = field(default_factory=dict)
    _position_strategies: dict[InstrumentId, StrategyId] = field(default_factory=dict)
    _last_prices: dict[InstrumentId, Decimal] = field(default_factory=dict)
    _realizations: list[RealizationEvent] = field(default_factory=list)
    _dividend_events: list[DividendEvent] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def empty(cls, starting_capital: Money) -> Portfolio:
        if starting_capital.amount <= 0:
            raise ValueError(
                f"Portfolio.empty starting_capital must be > 0, got {starting_capital.amount}"
            )
        zero = Money(Decimal(0), starting_capital.currency)
        return cls(
            _cash=starting_capital,
            _realized_gross=zero,
            _realized_after_tax=zero,
            _dividends_gross=zero,
            _dividends_after_tax=zero,
        )

    @property
    def currency(self) -> Currency:
        return self._cash.currency

    # ------------------------------------------------------------------
    # PortfolioView Protocol surface (read-only)
    # ------------------------------------------------------------------

    def cash(self) -> Money:
        return self._cash

    def equity(self) -> Money:
        return self.equity_after_tax()

    def equity_after_tax(self) -> Money:
        """Canonical performance reference (REQ_F_PRT_001).

        equity_after_tax = cash + marked - tax_liability_unpaid

        Cash captures every gross flow (BUY pays out, SELL takes in
        gross proceeds, dividends credit gross). The tax liability on
        realized gains and dividends is computed but not deducted from
        cash at realization time (France CTO pays tax annually, not at
        sale). This method subtracts the implicit liability so the
        canonical equity series is what the operator would walk away
        with after settling tax.

        Every open position MUST have a price recorded via ``mark`` —
        a missing price is a programmer error and panics. The
        backtester refreshes prices on every tick before any consumer
        reads equity.
        """
        marked = self._marked_value()
        tax_owed = (self._realized_gross - self._realized_after_tax) + (
            self._dividends_gross - self._dividends_after_tax
        )
        return self._cash + marked - tax_owed

    def equity_gross(self) -> Money:
        """Pre-tax equity = cash + marked. Realized and dividends are
        already reflected in cash (gross flows); they are informational
        running totals, not separate components of equity."""
        return self._cash + self._marked_value()

    def _marked_value(self) -> Money:
        """Mark every open position at its last recorded price."""
        marked = Money(Decimal(0), self.currency)
        for iid, pos in self._positions.items():
            price = self._last_prices.get(iid)
            assert price is not None, (
                f"Portfolio._marked_value: missing mark price for {iid}; "
                "call .mark() before reading equity"
            )
            marked = marked + Money(price * pos.quantity, self.currency)
        return marked

    def exposure_pct(self, bucket: AllocationBucket) -> Decimal:
        """Share of after-tax equity allocated to ``bucket``
        (REQ_F_PRT_003)."""
        eq = self.equity_after_tax().amount
        if eq <= 0:
            return Decimal(0)
        marked = Decimal(0)
        for iid, pos in self._positions.items():
            if self._position_buckets.get(iid) is not bucket:
                continue
            price = self._last_prices.get(iid)
            assert price is not None, f"Portfolio.exposure_pct: missing mark price for {iid}"
            marked += abs(price * pos.quantity)
        return marked / eq

    def holds(self, instrument_id: InstrumentId) -> bool:
        return instrument_id in self._positions

    def position_for(self, instrument_id: InstrumentId) -> Option[Position]:
        pos = self._positions.get(instrument_id)
        return Some(pos) if pos is not None else Nothing()

    # Read-only accessors used by analytics, dashboards, and tests.

    def realized_gross(self) -> Money:
        return self._realized_gross

    def realized_after_tax(self) -> Money:
        return self._realized_after_tax

    def dividends_gross(self) -> Money:
        return self._dividends_gross

    def dividends_after_tax(self) -> Money:
        return self._dividends_after_tax

    def positions(self) -> dict[InstrumentId, Position]:
        return dict(self._positions)

    def has_turbo_on(self, underlying: InstrumentId) -> bool:
        """``True`` iff a turbo position is open on ``underlying``.

        Used by the structured-products admission gate to enforce
        REQ_F_STP_007 (no SP / turbo stack on the same underlying)
        and by future risk checks that need to spot existing turbo
        exposure.
        """
        for pos in self._positions.values():
            if not isinstance(pos.instrument, Turbo):
                continue
            if pos.instrument.underlying == underlying:
                return True
        return False

    def issuer_concentration(self, issuer: str) -> Decimal:
        """Share of after-tax equity in structured products from
        ``issuer`` (REQ_F_STP_006).

        Returns a fraction in ``[0, 1]``. Empty equity yields zero
        — the admission caller still rejects via its own cap, no
        divide-by-zero leaks here.
        """
        eq = self.equity_after_tax().amount
        if eq <= 0:
            return Decimal(0)
        marked = Decimal(0)
        for iid, pos in self._positions.items():
            if not isinstance(pos.instrument, StructuredProduct):
                continue
            if pos.instrument.issuer != issuer:
                continue
            price = self._last_prices.get(iid)
            assert price is not None, (
                f"Portfolio.issuer_concentration: missing mark price for {iid}"
            )
            marked += abs(price * pos.quantity)
        return marked / eq

    def realizations(self) -> tuple[RealizationEvent, ...]:
        """Append-only log of every realization (close / partial close /
        knockout). Consumed by analytics for attribution."""
        return tuple(self._realizations)

    def dividend_events(self) -> tuple[DividendEvent, ...]:
        """Append-only log of dividend credits with strategy
        attribution."""
        return tuple(self._dividend_events)

    def attribution(self) -> tuple[AttributionRow, ...]:
        """Aggregate realization + dividend events into Phase-6
        NAV / by-strategy / by-class attribution rows
        (REQ_F_PRT_002).

        Rows are emitted in stable order: NAV first, then strategies
        sorted by id, then classes sorted by value. A strategy or
        class with no recorded events is skipped — empty rows are
        not emitted.
        """
        zero = Money(Decimal(0), self.currency)
        # Aggregate by strategy.
        by_strategy_gross: dict[StrategyId, Money] = {}
        by_strategy_net: dict[StrategyId, Money] = {}
        by_strategy_div_gross: dict[StrategyId, Money] = {}
        by_strategy_div_net: dict[StrategyId, Money] = {}
        for r in self._realizations:
            by_strategy_gross[r.strategy] = (
                by_strategy_gross.get(r.strategy, zero) + r.realized_gross
            )
            by_strategy_net[r.strategy] = (
                by_strategy_net.get(r.strategy, zero) + r.realized_after_tax
            )
        for d in self._dividend_events:
            by_strategy_div_gross[d.strategy] = (
                by_strategy_div_gross.get(d.strategy, zero) + d.gross
            )
            by_strategy_div_net[d.strategy] = (
                by_strategy_div_net.get(d.strategy, zero) + d.after_tax
            )
        # Aggregate by instrument class.
        by_class_gross: dict[InstrumentClass, Money] = {}
        by_class_net: dict[InstrumentClass, Money] = {}
        by_class_div_gross: dict[InstrumentClass, Money] = {}
        by_class_div_net: dict[InstrumentClass, Money] = {}
        for r in self._realizations:
            by_class_gross[r.instrument_class] = (
                by_class_gross.get(r.instrument_class, zero) + r.realized_gross
            )
            by_class_net[r.instrument_class] = (
                by_class_net.get(r.instrument_class, zero) + r.realized_after_tax
            )
        for d in self._dividend_events:
            by_class_div_gross[d.instrument_class] = (
                by_class_div_gross.get(d.instrument_class, zero) + d.gross
            )
            by_class_div_net[d.instrument_class] = (
                by_class_div_net.get(d.instrument_class, zero) + d.after_tax
            )

        nav_value = self.equity_after_tax() if self._positions or self.equity_curve else self._cash
        rows: list[AttributionRow] = [
            AttributionRow(
                kind="nav",
                label="NAV",
                realized_gross=self._realized_gross,
                realized_after_tax=self._realized_after_tax,
                dividends_gross=self._dividends_gross,
                dividends_after_tax=self._dividends_after_tax,
                nav_after_tax=nav_value,
            )
        ]
        all_strategies = sorted(
            set(by_strategy_gross) | set(by_strategy_div_gross),
            key=str,
        )
        for sid in all_strategies:
            rows.append(
                AttributionRow(
                    kind="strategy",
                    label=str(sid),
                    realized_gross=by_strategy_gross.get(sid, zero),
                    realized_after_tax=by_strategy_net.get(sid, zero),
                    dividends_gross=by_strategy_div_gross.get(sid, zero),
                    dividends_after_tax=by_strategy_div_net.get(sid, zero),
                )
            )
        all_classes = sorted(
            set(by_class_gross) | set(by_class_div_gross),
            key=lambda c: c.value,
        )
        for cls in all_classes:
            rows.append(
                AttributionRow(
                    kind="class",
                    label=cls.value,
                    realized_gross=by_class_gross.get(cls, zero),
                    realized_after_tax=by_class_net.get(cls, zero),
                    dividends_gross=by_class_div_gross.get(cls, zero),
                    dividends_after_tax=by_class_div_net.get(cls, zero),
                )
            )
        return tuple(rows)

    # ------------------------------------------------------------------
    # Mutating operations
    # ------------------------------------------------------------------

    def mark(self, prices: dict[InstrumentId, Decimal]) -> None:
        """Refresh marking-to-market prices in bulk."""
        for iid, price in prices.items():
            assert price > 0, f"Portfolio.mark: price must be > 0, got {price} for {iid}"
            self._last_prices[iid] = price

    def apply(  # noqa: PLR0915 - direct translation of SDD §8 apply
        self,
        trade: Trade,
        order: Order,
        bucket: AllocationBucket,
        tax: TaxConfig,
    ) -> None:
        """Apply a fill to the portfolio.

        ``order`` is required because ``Trade`` carries only
        ``order_id`` (not the originating Order); the engine knows
        the Order at call time and supplies it explicitly. ``bucket``
        identifies the allocation bucket the originating strategy
        belongs to (used by ``exposure_pct``).

        Invariants:
        - Cash impact: BUY decreases cash by ``price x qty + fees``;
          SELL increases by ``price x qty - fees``.
        - Realization: opposing fill realizes PnL on the overlap
          quantity; tax is applied at realization (REQ_F_BCT_006).
        - Direction flip: a fill that overshoots an opposing position
          closes the existing one and opens a new one in the new
          direction at the trade price.
        """
        if trade.fees.currency != self.currency:
            raise ValueError(
                f"Trade.fees.currency must match Portfolio.currency, "
                f"got {trade.fees.currency} vs {self.currency}"
            )
        if order.instrument.currency != self.currency:
            raise ValueError(
                f"Order.instrument.currency must match Portfolio.currency, "
                f"got {order.instrument.currency} vs {self.currency}"
            )
        assert trade.order_id == order.id, (
            f"Trade.order_id ({trade.order_id}) must match Order.id ({order.id})"
        )

        iid = order.instrument.id
        qty = trade.quantity_filled
        price = trade.price
        fees = trade.fees
        notional = Money(price * qty, self.currency)

        # Cash impact (REQ_SDD_DAT_005: executed fees from Trade.fees).
        if order.side is Side.BUY:
            self._cash = self._cash - notional - fees
        else:
            self._cash = self._cash + notional - fees

        signed_trade_qty = qty if order.side is Side.BUY else -qty
        cur = self._positions.get(iid)

        if cur is None:
            # Open
            self._positions[iid] = Position(
                instrument=order.instrument,
                quantity=signed_trade_qty,
                avg_price=price,
                opened_at=trade.executed_at,
                stop_loss=order.stop_loss,
            )
            self._position_buckets[iid] = bucket
            self._position_strategies[iid] = order.source_strategy
            self._last_prices[iid] = price
            return

        same_dir = (cur.quantity > 0 and signed_trade_qty > 0) or (
            cur.quantity < 0 and signed_trade_qty < 0
        )
        if same_dir:
            # Increase: weighted-average avg_price.
            new_qty = cur.quantity + signed_trade_qty
            new_avg = (cur.avg_price * abs(cur.quantity) + price * qty) / abs(new_qty)
            self._positions[iid] = Position(
                instrument=cur.instrument,
                quantity=new_qty,
                avg_price=new_avg,
                opened_at=cur.opened_at,
                stop_loss=order.stop_loss,
            )
            self._last_prices[iid] = price
            return

        # Opposing fill: realize PnL on the overlap.
        overlap = min(abs(cur.quantity), qty)
        if cur.quantity > 0:  # closing long
            gross_pnl = (price - cur.avg_price) * overlap
        else:  # closing short
            gross_pnl = (cur.avg_price - price) * overlap
        gross_money = Money(gross_pnl, self.currency)
        net_money = net_gain(tax, gross_money)
        self._realized_gross = self._realized_gross + gross_money
        self._realized_after_tax = self._realized_after_tax + net_money
        # Attribution: realization is credited to the position's
        # originating strategy + instrument class, not the strategy
        # that issued the *closing* order. This matches institutional
        # convention (P&L attributed to the strategy that opened the
        # exposure).
        opener = self._position_strategies.get(iid, order.source_strategy)
        self._realizations.append(
            RealizationEvent(
                at=trade.executed_at,
                strategy=opener,
                instrument_class=cur.instrument.cls,
                realized_gross=gross_money,
                realized_after_tax=net_money,
            )
        )

        new_signed = cur.quantity + signed_trade_qty
        if new_signed == 0:
            # Fully closed
            del self._positions[iid]
            del self._position_buckets[iid]
            del self._position_strategies[iid]
            self._last_prices[iid] = price
            return

        # Direction flip: leftover opens a new position at the trade
        # price; the new exposure is attributed to the flipping
        # order's strategy.
        if (new_signed > 0) != (cur.quantity > 0):
            self._positions[iid] = Position(
                instrument=cur.instrument,
                quantity=new_signed,
                avg_price=price,
                opened_at=trade.executed_at,
                stop_loss=order.stop_loss,
            )
            self._position_strategies[iid] = order.source_strategy
        else:
            # Partial close: avg_price unchanged; opener strategy
            # stays mapped to this iid.
            self._positions[iid] = Position(
                instrument=cur.instrument,
                quantity=new_signed,
                avg_price=cur.avg_price,
                opened_at=cur.opened_at,
                stop_loss=order.stop_loss,
            )
        self._last_prices[iid] = price

    def apply_dividend(
        self,
        instrument_id: InstrumentId,
        amount_gross: Money,
        tax: TaxConfig,
        at: datetime,
    ) -> None:
        """Credit a cash dividend; track gross/net totals (REQ_F_TAX_002,
        REQ_F_BCT_005).

        Cash is credited the GROSS amount (consistent with how SELL
        proceeds enter cash gross); the implicit tax liability is
        accumulated in ``dividends_gross - dividends_after_tax`` and
        deducted by ``equity_after_tax``. This matches the France CTO
        regime where tax settles annually, not at receipt.

        ``at`` is the dividend's pay date; the simulator passes the
        tick timestamp so attribution events carry the correct
        wall-clock event time.

        Caller is responsible for sizing ``amount_gross`` to the held
        share count (``DividendSimulator`` does this in the backtester).
        Dividends are credited only on long positions; calling this for
        a non-long holding is a programmer error and panics.
        """
        if amount_gross.currency != self.currency:
            raise ValueError(
                f"Dividend.amount_gross.currency must match Portfolio.currency, "
                f"got {amount_gross.currency} vs {self.currency}"
            )
        pos = self._positions.get(instrument_id)
        assert pos is not None and pos.quantity > 0, (
            f"apply_dividend: {instrument_id} not held long"
        )
        net = net_dividend(tax, amount_gross)
        self._cash = self._cash + amount_gross
        self._dividends_gross = self._dividends_gross + amount_gross
        self._dividends_after_tax = self._dividends_after_tax + net
        # Attribution: dividends are credited to the strategy that
        # opened the underlying position.
        opener = self._position_strategies.get(instrument_id, StrategyId(""))
        self._dividend_events.append(
            DividendEvent(
                at=at,
                strategy=opener,
                instrument_class=pos.instrument.cls,
                gross=amount_gross,
                after_tax=net,
            )
        )

    def inject(self, amount: Money) -> None:
        """Receive an external capital injection (REQ_F_BCT_007).

        Increases cash by ``amount``. Performance metrics exclude
        injections via ``capital_flow.equity_excl_injections``; this
        method is purely a cash-balance update.
        """
        if amount.currency != self.currency:
            raise ValueError(
                f"Portfolio.inject: amount.currency must match "
                f"Portfolio.currency, got {amount.currency} vs {self.currency}"
            )
        if amount.amount <= 0:
            raise ValueError(f"Portfolio.inject: amount must be > 0, got {amount.amount}")
        self._cash = self._cash + amount

    def close_at_zero(self, instrument_id: InstrumentId, tax: TaxConfig, at: datetime) -> None:
        """Close a position at zero (turbo knockout — REQ_F_TRB_005,
        REQ_F_BCT_004).

        Realizes a loss equal to the remaining cost basis; the position
        is removed. Cash is unchanged because the closing fill price is
        zero. The position's ``last_price`` is set to zero so any
        equity read between knockout and the next mark is consistent.

        ``at`` is the knockout tick's timestamp; recorded on the
        attribution event.
        """
        pos = self._positions.get(instrument_id)
        assert pos is not None, f"close_at_zero: {instrument_id} not held"
        # Loss on the closed slice: -avg_price * quantity (positive qty
        # for long, negative for short — quantity * avg_price is the
        # signed cost basis; closing at 0 produces -cost_basis).
        cost_basis = pos.avg_price * pos.quantity
        gross_loss = Money(-cost_basis, self.currency)
        net_loss = net_gain(tax, gross_loss)
        self._realized_gross = self._realized_gross + gross_loss
        # net_gain on a loss passes through unchanged (REQ_F_TAX_001
        # loss handling).
        self._realized_after_tax = self._realized_after_tax + net_loss
        opener = self._position_strategies.get(instrument_id, StrategyId(""))
        self._realizations.append(
            RealizationEvent(
                at=at,
                strategy=opener,
                instrument_class=pos.instrument.cls,
                realized_gross=gross_loss,
                realized_after_tax=net_loss,
            )
        )
        del self._positions[instrument_id]
        del self._position_buckets[instrument_id]
        del self._position_strategies[instrument_id]
        # Drop the cached mark — the position is gone; if a stale
        # entry stayed around it would be a footgun for the next
        # caller of equity_after_tax.
        self._last_prices.pop(instrument_id, None)

    def record_equity(self, at: datetime) -> EquityPoint:
        """Snapshot equity at ``at`` and append to ``equity_curve``.

        ``drawdown_pct`` is computed against the running peak of the
        after-tax curve so the engine doesn't need a separate metrics
        helper.
        """
        eq_after = self.equity_after_tax()
        eq_gross = self.equity_gross()
        peak = eq_after.amount
        for p in self.equity_curve:
            peak = max(peak, p.equity_after_tax.amount)
        dd = Decimal(0) if peak <= 0 else max(Decimal(0), (peak - eq_after.amount) / peak)
        point = EquityPoint(
            at=at,
            equity_gross=eq_gross,
            equity_after_tax=eq_after,
            drawdown_pct=dd,
        )
        self.equity_curve.append(point)
        return point
