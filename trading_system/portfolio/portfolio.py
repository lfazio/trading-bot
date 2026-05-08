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
from trading_system.models.identifiers import InstrumentId
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket
from trading_system.models.trading import Order, Position, Side, Trade
from trading_system.result import Nothing, Option, Some
from trading_system.tax.config import TaxConfig
from trading_system.tax.engine import net_dividend, net_gain


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
    _last_prices: dict[InstrumentId, Decimal] = field(default_factory=dict)
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

    # ------------------------------------------------------------------
    # Mutating operations
    # ------------------------------------------------------------------

    def mark(self, prices: dict[InstrumentId, Decimal]) -> None:
        """Refresh marking-to-market prices in bulk."""
        for iid, price in prices.items():
            assert price > 0, f"Portfolio.mark: price must be > 0, got {price} for {iid}"
            self._last_prices[iid] = price

    def apply(
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
        self._realized_gross = self._realized_gross + gross_money
        self._realized_after_tax = self._realized_after_tax + net_gain(tax, gross_money)

        new_signed = cur.quantity + signed_trade_qty
        if new_signed == 0:
            # Fully closed
            del self._positions[iid]
            del self._position_buckets[iid]
            self._last_prices[iid] = price
            return

        # Direction flip: leftover opens a new position at the trade price.
        if (new_signed > 0) != (cur.quantity > 0):
            self._positions[iid] = Position(
                instrument=cur.instrument,
                quantity=new_signed,
                avg_price=price,
                opened_at=trade.executed_at,
                stop_loss=order.stop_loss,
            )
        else:
            # Partial close: avg_price unchanged.
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
    ) -> None:
        """Credit a cash dividend; track gross/net totals (REQ_F_TAX_002,
        REQ_F_BCT_005).

        Cash is credited the GROSS amount (consistent with how SELL
        proceeds enter cash gross); the implicit tax liability is
        accumulated in ``dividends_gross - dividends_after_tax`` and
        deducted by ``equity_after_tax``. This matches the France CTO
        regime where tax settles annually, not at receipt.

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

    def close_at_zero(self, instrument_id: InstrumentId, tax: TaxConfig) -> None:
        """Close a position at zero (turbo knockout — REQ_F_TRB_005,
        REQ_F_BCT_004).

        Realizes a loss equal to the remaining cost basis; the position
        is removed. Cash is unchanged because the closing fill price is
        zero. The position's ``last_price`` is set to zero so any
        equity read between knockout and the next mark is consistent.
        """
        pos = self._positions.get(instrument_id)
        assert pos is not None, f"close_at_zero: {instrument_id} not held"
        # Loss on the closed slice: -avg_price * quantity (positive qty
        # for long, negative for short — quantity * avg_price is the
        # signed cost basis; closing at 0 produces -cost_basis).
        cost_basis = pos.avg_price * pos.quantity
        gross_loss = Money(-cost_basis, self.currency)
        self._realized_gross = self._realized_gross + gross_loss
        # net_gain on a loss passes through unchanged (REQ_F_TAX_001
        # loss handling).
        self._realized_after_tax = self._realized_after_tax + net_gain(tax, gross_loss)
        del self._positions[instrument_id]
        del self._position_buckets[instrument_id]
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
