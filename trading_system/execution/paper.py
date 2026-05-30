"""``PaperBrokerAdapter`` — paper-trading broker (CR-025).

A concrete ``BrokerAdapter`` that simulates fills against **live**
market prices read through a wrapped ``MarketDataProvider`` (typically
``YFinanceMarketDataProvider``). No credentials — paper trading is
in-process simulation against a public data feed; there is nothing
to authenticate.

The adapter wraps a ``LocalBrokerAdapter`` for the in-process
position / cash / fee / slippage state machine and delegates price
discovery to the configured ``MarketDataProvider``. Inside
``submit(order)``:

  1. Call ``market_data.latest(order.instrument)``.
  2. On ``Ok(bar)``, synthesise a ``Tick`` from the bar's close
     (``bid = close * (1 - spread/2)``, ``ask = close * (1 +
     spread/2)``, ``last = close``) and feed it through the
     wrapped ``LocalBrokerAdapter.process_tick``.
  3. Delegate the actual fill to ``LocalBrokerAdapter.submit``,
     which runs the slippage + fee model + position update.
  4. On ``Err(reason)``, surface as
     ``broker:no_market_data:<instrument.id>``.

Same shape as the live-broker adapters that ship in their own SRS
amendments per ``REQ_F_BRK_003`` — operators run paper trading
through the same ``LiveTradingRuntime`` (CR-019 step 2) as real-
broker sessions. The legacy ``webapp/runtimes/paper_trading.py``
is deprecated and scheduled for removal once operators migrate.

REQ refs: REQ_F_PAP_011..014, REQ_SDD_PAP_001..005,
REQ_F_BRK_001..005.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.execution.adapter import Subscription
from trading_system.execution.fees import FeeModel
from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.slippage import SlippageModel
from trading_system.execution.types import Account, Tick
from trading_system.models.identifiers import OrderId
from trading_system.models.instrument import Instrument
from trading_system.models.money import Money
from trading_system.models.trading import Order, Position
from trading_system.result import Err, Ok, Option, Result


@runtime_checkable
class _LatestProvider(Protocol):
    """Minimal slice of the ``MarketDataProvider`` Protocol the
    paper broker actually consults (just ``latest``)."""

    def latest(self, instrument: Instrument) -> object: ...


@dataclass(slots=True)
class PaperBrokerAdapter:
    """In-process broker simulator wrapped around a live data feed.

    Construction parameters:
      - ``starting_cash`` — opening cash balance + currency anchor.
      - ``market_data`` — any object satisfying the ``latest``
        method on the ``MarketDataProvider`` Protocol (REQ_F_DAT_001
        family). Production deployments wire
        ``YFinanceMarketDataProvider``; tests pass a stub.
      - ``fee_model`` — drives realized fees per fill.
      - ``slippage_model`` — drives execution noise.
      - ``seed`` — RNG seed used by the wrapped slippage model.
      - ``spread_bps`` — synthetic bid/ask spread applied around
        each fetched close (defaults to 0 — operators tune for
        the wrapping data feed's typical spread).

    Internals: the paper broker holds a private
    ``LocalBrokerAdapter`` for the position + cash + fee +
    slippage state machine. The only difference from the
    operator's standpoint is that prices come from the wrapped
    ``MarketDataProvider`` rather than from
    ``process_tick``-fed external bars.
    """

    starting_cash: Money
    market_data: _LatestProvider
    fee_model: FeeModel
    slippage_model: SlippageModel
    seed: int = 0
    spread_bps: Decimal = field(default_factory=lambda: Decimal(0))

    _inner: LocalBrokerAdapter = field(init=False)

    def __post_init__(self) -> None:
        if self.starting_cash.amount < 0:
            raise ValueError(
                f"PaperBrokerAdapter.starting_cash must be >= 0, "
                f"got {self.starting_cash.amount}"
            )
        if self.spread_bps < 0:
            raise ValueError(
                f"PaperBrokerAdapter.spread_bps must be >= 0, "
                f"got {self.spread_bps}"
            )
        self._inner = LocalBrokerAdapter(
            starting_cash=self.starting_cash,
            fee_model=self.fee_model,
            slippage_model=self.slippage_model,
            seed=self.seed,
        )

    # ------------------------------------------------------------------
    # Test / harness helpers
    # ------------------------------------------------------------------

    def register_instrument(self, instrument: Instrument) -> None:
        """Pass-through to the inner LocalBrokerAdapter so
        ``instrument(symbol)`` resolves."""
        self._inner.register_instrument(instrument)

    # ------------------------------------------------------------------
    # BrokerAdapter Protocol — REQ_F_BRK_001
    # ------------------------------------------------------------------

    def submit(self, order: Order) -> Result[OrderId, str]:
        """REQ_SDD_PAP_002 — fetch latest price from the wrapped
        ``MarketDataProvider``, seed it as a ``Tick`` on the inner
        adapter, delegate the fill."""
        result = self.market_data.latest(order.instrument)
        if isinstance(result, Err):
            return Err(
                f"broker:no_market_data:{order.instrument.id}"
            )
        if not isinstance(result, Ok):
            return Err(
                f"broker:no_market_data:{order.instrument.id}"
            )
        bar = result.value
        close = getattr(bar, "close", None)
        bar_at = getattr(bar, "at", None)
        if close is None or bar_at is None:
            return Err(
                f"broker:no_market_data:{order.instrument.id}"
            )
        # Synthesise a tick from the bar's close.
        half_spread = (Decimal(close) * self.spread_bps) / Decimal(20000)
        bid = Decimal(close) - half_spread
        ask = Decimal(close) + half_spread
        # Bid SHALL be > 0 — defensive guard against very small
        # prices + large configured spreads.
        if bid <= 0:
            return Err(
                f"broker:no_market_data:{order.instrument.id}"
            )
        tick = Tick(
            instrument_id=order.instrument.id,
            at=bar_at,
            bid=bid,
            ask=ask,
            last=Decimal(close),
        )
        self._inner.process_tick(tick)
        return self._inner.submit(order)

    def cancel(self, order_id: OrderId) -> Result[bool, str]:
        return self._inner.cancel(order_id)

    def positions(self) -> list[Position]:
        return self._inner.positions()

    def account_state(self) -> Account:
        """REQ_SDD_PAP_003 — refresh the inner adapter's per-position
        latest tick from the wrapped ``MarketDataProvider`` before
        computing equity, so the unrealized P&L reflects live prices
        (not just the most recent ``submit`` time)."""
        for position in self._inner.positions():
            try:
                latest = self.market_data.latest(position.instrument)
            except Exception:  # noqa: BLE001 — paper boundary
                continue
            if not isinstance(latest, Ok):
                continue
            bar = latest.value
            close = getattr(bar, "close", None)
            bar_at = getattr(bar, "at", None)
            if close is None or bar_at is None:
                continue
            self._inner.process_tick(
                Tick(
                    instrument_id=position.instrument.id,
                    at=bar_at,
                    bid=Decimal(close),
                    ask=Decimal(close),
                    last=Decimal(close),
                )
            )
        return self._inner.account_state()

    def instrument(self, symbol: str) -> Option[Instrument]:
        return self._inner.instrument(symbol)

    def subscribe(
        self, symbols: list[str], on_tick: Callable[[Tick], None]
    ) -> Subscription:
        """Paper broker has no native tick stream — operators feed
        ticks externally via the tick driver. Subscribe is a thin
        pass-through to the inner LocalBrokerAdapter so the conformance
        suite passes; the v1 paper-trading runtime does NOT use
        broker-side subscriptions (it polls market_data directly)."""
        return self._inner.subscribe(symbols, on_tick)
