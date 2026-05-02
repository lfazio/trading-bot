"""``LocalBrokerAdapter`` — in-process deterministic broker simulator.

Runs the broker contract entirely in memory: orders submitted are
filled against a tick stream supplied via ``process_tick``, with
deterministic fees (``FeeModel``) and slippage (``SlippageModel``).
The adapter is the conformance baseline for any future live-broker
adapter (REQ_F_BRK_002).

Scope of this revision:

- MARKET orders fill immediately at submit time using the most recent
  tick for the order's instrument (post-slippage).
- LIMIT and STOP orders are rejected with
  ``broker:order_unsupported`` for now; a follow-up step adds the
  pending-queue resolver. Submit / cancel idempotency
  (REQ_SDD_API_006) is in place for future use.
- ``subscribe`` registers a callback per symbol; ticks fed via
  ``process_tick`` are dispatched to matching subscribers.

REQ refs:
- REQ_F_BRK_001..005 — Protocol implementation, runtime-checkable.
- REQ_SDS_INT_001 — conformance suite parametrizes over this adapter.
- REQ_SDD_API_002 / REQ_SDD_API_006 — Protocol shape; submit/cancel
  idempotency on a caller-supplied ``Order.id``.
- REQ_SDD_ERR_002 — categorized ``Err`` strings.
- REQ_F_TRB_005 — turbo positions risk = invested capital only (no
  margin modelling here; the adapter records a position and updates
  cash by the executed notional).
"""

from __future__ import annotations

import contextlib
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from trading_system.execution.adapter import Subscription
from trading_system.execution.fees import FeeModel
from trading_system.execution.slippage import SlippageModel
from trading_system.execution.types import Account, Tick
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    TradeId,
)
from trading_system.models.instrument import Instrument
from trading_system.models.money import Currency, Money
from trading_system.models.trading import (
    Order,
    OrderType,
    Position,
    Side,
    Trade,
)
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@dataclass(slots=True)
class _SubscriptionHandle:
    """Concrete Subscription returned by ``LocalBrokerAdapter.subscribe``."""

    _adapter: LocalBrokerAdapter
    _symbols: tuple[str, ...]
    _callback: Callable[[Tick], None]
    _cancelled: bool = False

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._adapter._unsubscribe(self)


@dataclass(slots=True)
class LocalBrokerAdapter:
    """In-process broker simulator (the lifecycle's reference adapter).

    Construction parameters:
      - ``starting_cash`` — opening cash balance and currency anchor
        for all account fields.
      - ``fee_model`` — drives realized fees per fill.
      - ``slippage_model`` — drives execution noise.
      - ``seed`` — RNG seed used by the slippage model
        (REQ_SDS_ARC_005).

    Concrete state (private):
      - ``_orders`` — every order keyed by its caller-supplied id.
      - ``_filled`` — set of order ids already filled.
      - ``_cancelled`` — set of cancelled order ids.
      - ``_positions`` — ``InstrumentId -> Position``.
      - ``_instrument_book`` — ``symbol -> Instrument`` populated via
        ``register_instrument`` so ``instrument()`` lookups succeed.
      - ``_latest_tick`` — most recent tick per instrument (drives
        MARKET fills and mark-to-market).
      - ``_subscriptions`` — active tick subscriptions.
    """

    starting_cash: Money
    fee_model: FeeModel
    slippage_model: SlippageModel
    seed: int = 0

    _cash: Money = field(init=False)
    _realized_pnl: Money = field(init=False)
    _orders: dict[OrderId, Order] = field(default_factory=dict, init=False)
    _filled: set[OrderId] = field(default_factory=set, init=False)
    _cancelled: set[OrderId] = field(default_factory=set, init=False)
    _trades: list[Trade] = field(default_factory=list, init=False)
    _positions: dict[InstrumentId, Position] = field(default_factory=dict, init=False)
    _instrument_book: dict[str, Instrument] = field(default_factory=dict, init=False)
    _latest_tick: dict[InstrumentId, Tick] = field(default_factory=dict, init=False)
    _subscriptions: list[_SubscriptionHandle] = field(default_factory=list, init=False)
    _next_trade_seq: int = field(default=0, init=False)
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        if self.starting_cash.amount < 0:
            raise ValueError(
                f"LocalBrokerAdapter.starting_cash must be >= 0, got {self.starting_cash.amount}"
            )
        self._cash = self.starting_cash
        self._realized_pnl = Money(Decimal(0), self.starting_cash.currency)
        self._rng = random.Random(self.seed)

    # ------------------------------------------------------------------
    # Test / harness helpers
    # ------------------------------------------------------------------

    def register_instrument(self, instrument: Instrument) -> None:
        """Register an instrument so ``instrument(symbol)`` resolves."""
        self._instrument_book[instrument.symbol] = instrument

    def process_tick(self, tick: Tick) -> None:
        """Drive the simulator with an external tick. Updates the
        latest-tick cache and dispatches to subscribers. (LIMIT/STOP
        resolution will hook in here in the next step.)"""
        self._latest_tick[tick.instrument_id] = tick
        for sub in list(self._subscriptions):
            if sub._cancelled:
                continue
            if not sub._symbols or any(s == "*" for s in sub._symbols):
                sub._callback(tick)
                continue
            symbol = self._symbol_of(tick.instrument_id)
            if symbol in sub._symbols:
                sub._callback(tick)

    # ------------------------------------------------------------------
    # BrokerAdapter Protocol
    # ------------------------------------------------------------------

    def submit(self, order: Order) -> Result[OrderId, str]:
        # Idempotency on caller-supplied client id (REQ_SDD_API_006).
        if order.id in self._orders:
            return Ok(order.id)
        if order.type is not OrderType.MARKET:
            return Err(
                f"broker:order_unsupported: {order.type.value} orders are "
                "not yet supported by LocalBrokerAdapter"
            )
        tick = self._latest_tick.get(order.instrument.id)
        if tick is None:
            return Err(
                f"broker:no_market_data: no tick seen for "
                f"{order.instrument.id}; call process_tick first"
            )
        currency = order.instrument.currency
        if currency != self._cash.currency:
            return Err(
                f"broker:currency_mismatch: instrument currency {currency} "
                f"!= account currency {self._cash.currency}"
            )

        reference = tick.ask if order.side is Side.BUY else tick.bid
        slippage = self.slippage_model.slip(order, reference, self._rng)
        fill_price = reference + slippage
        if fill_price <= 0:
            return Err(f"broker:bad_fill: computed fill_price {fill_price} <= 0")

        fees = self.fee_model.fees(order, fill_price)
        notional = Money(order.quantity * fill_price, currency)
        signed_qty = order.quantity if order.side is Side.BUY else -order.quantity

        # Record order, generate trade.
        self._orders[order.id] = order
        self._next_trade_seq += 1
        trade = Trade(
            id=TradeId(f"t-{self._next_trade_seq:08d}"),
            order_id=order.id,
            executed_at=tick.at,
            price=fill_price,
            quantity_filled=order.quantity,
            fees=fees,
            slippage=slippage,
        )
        self._trades.append(trade)
        self._filled.add(order.id)

        # Apply to positions and cash.
        self._apply_fill(order, signed_qty, fill_price, fees, notional)
        return Ok(order.id)

    def cancel(self, order_id: OrderId) -> Result[bool, str]:
        if order_id in self._cancelled:
            return Ok(False)  # already cancelled — idempotent
        if order_id in self._filled:
            return Err(f"broker:already_filled: cannot cancel {order_id}")
        if order_id not in self._orders:
            return Err(f"broker:not_found: unknown order {order_id}")
        # Pending order present but not filled: queued LIMIT/STOP. Mark
        # cancelled. (No queue exists yet; this branch becomes useful
        # when LIMIT/STOP support lands.)
        self._cancelled.add(order_id)
        return Ok(True)

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def account_state(self) -> Account:
        unrealized = self._unrealized_pnl()
        positions_value = self._positions_market_value()
        equity = self._cash + positions_value
        return Account(
            cash=self._cash,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=unrealized,
            equity=equity,
        )

    def instrument(self, symbol: str) -> Option[Instrument]:
        instr = self._instrument_book.get(symbol)
        return Some(instr) if instr is not None else Nothing()

    def subscribe(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> Subscription:
        handle = _SubscriptionHandle(
            _adapter=self,
            _symbols=tuple(symbols),
            _callback=on_tick,
        )
        self._subscriptions.append(handle)
        return handle

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _unsubscribe(self, handle: _SubscriptionHandle) -> None:
        with contextlib.suppress(ValueError):
            self._subscriptions.remove(handle)

    def _symbol_of(self, instrument_id: InstrumentId) -> str:
        for symbol, instrument in self._instrument_book.items():
            if instrument.id == instrument_id:
                return symbol
        return ""

    def _apply_fill(
        self,
        order: Order,
        signed_qty: Decimal,
        fill_price: Decimal,
        fees: Money,
        notional: Money,
    ) -> None:
        """Update cash, position, and realized PnL for a fill."""
        currency = order.instrument.currency
        instrument_id = order.instrument.id
        existing = self._positions.get(instrument_id)

        # Cash: BUY pays out (cash -= notional + fees);
        # SELL takes in (cash += notional - fees).
        if order.side is Side.BUY:
            self._cash = self._cash - notional - fees
        else:
            self._cash = self._cash + notional - fees

        if existing is None:
            # Opening a new position.
            self._positions[instrument_id] = Position(
                instrument=order.instrument,
                quantity=signed_qty,
                avg_price=fill_price,
                opened_at=order.created_at,
                stop_loss=order.stop_loss,
            )
            return

        # Existing position: check if same direction (add) or opposite (close / flip).
        same_direction = (existing.quantity > 0 and signed_qty > 0) or (
            existing.quantity < 0 and signed_qty < 0
        )
        if same_direction:
            # Average price update.
            new_qty = existing.quantity + signed_qty
            total_cost = existing.quantity * existing.avg_price + signed_qty * fill_price
            new_avg = total_cost / new_qty
            self._positions[instrument_id] = Position(
                instrument=existing.instrument,
                quantity=new_qty,
                avg_price=new_avg,
                opened_at=existing.opened_at,
                stop_loss=existing.stop_loss,
            )
            return

        # Opposite direction: close (full or partial) or flip.
        closing_qty = min(abs(existing.quantity), abs(signed_qty))
        # Realized PnL for the closed slice (gross, pre-tax).
        # For a long being sold: (sell_price - avg) * closing_qty
        # For a short being bought: (avg - buy_price) * closing_qty
        if existing.quantity > 0:
            realized = (fill_price - existing.avg_price) * closing_qty
        else:
            realized = (existing.avg_price - fill_price) * closing_qty
        self._realized_pnl = self._realized_pnl + Money(realized, currency)

        remaining_existing = existing.quantity + signed_qty
        if remaining_existing == 0:
            del self._positions[instrument_id]
        elif (existing.quantity > 0) == (remaining_existing > 0):
            # Partial close — same direction remains.
            self._positions[instrument_id] = Position(
                instrument=existing.instrument,
                quantity=remaining_existing,
                avg_price=existing.avg_price,
                opened_at=existing.opened_at,
                stop_loss=existing.stop_loss,
            )
        else:
            # Flipped: opened a new opposite position at fill_price.
            self._positions[instrument_id] = Position(
                instrument=existing.instrument,
                quantity=remaining_existing,
                avg_price=fill_price,
                opened_at=order.created_at,
                stop_loss=order.stop_loss,
            )

    def _positions_market_value(self) -> Money:
        currency: Currency = self._cash.currency
        total = Decimal(0)
        for instrument_id, position in self._positions.items():
            tick = self._latest_tick.get(instrument_id)
            mark = tick.last if tick is not None else position.avg_price
            total += position.quantity * mark
        return Money(total, currency)

    def _unrealized_pnl(self) -> Money:
        currency = self._cash.currency
        total = Decimal(0)
        for instrument_id, position in self._positions.items():
            tick = self._latest_tick.get(instrument_id)
            mark = tick.last if tick is not None else position.avg_price
            total += position.quantity * (mark - position.avg_price)
        return Money(total, currency)
