"""``BacktestBroker`` — thin wrapper around ``LocalBrokerAdapter``.

The wrapper exists for ergonomic reasons: ``LocalBrokerAdapter.submit``
returns a ``Result[OrderId, str]`` (matching the BrokerAdapter Protocol)
and pushes the resulting fill onto an internal ``_trades`` list. The
backtest engine wants the ``Trade`` directly so it can apply the fill
to its canonical ``Portfolio``. ``BacktestBroker.submit`` returns
``Result[Trade, str]`` to spare every caller the same fishing pattern.

Per REQ_SDS_FLO_003, the live and backtest paths use the same
``BrokerAdapter`` Protocol; this wrapper does not change that — it
sits *above* the Protocol and is internal to ``backtesting/``.

REQ refs:
- REQ_F_BRK_001..005 — the underlying adapter is the conformance
  baseline; this wrapper does not weaken any invariant.
- REQ_SDS_FLO_003 — same trade-decision pipeline live and backtest.
- REQ_SDD_DAT_005 — ``Trade.fees`` is the executed fee returned by
  the adapter; the wrapper just relays.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.types import Tick
from trading_system.models.identifiers import InstrumentId
from trading_system.models.trading import Order, Trade
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@dataclass(slots=True)
class BacktestBroker:
    """Tick-driven facade over ``LocalBrokerAdapter``.

    Construction is by-reference: callers configure the underlying
    adapter (``starting_cash``, ``fee_model``, ``slippage_model``,
    ``seed``) and pass it in; the wrapper does not own adapter
    lifecycle.
    """

    adapter: LocalBrokerAdapter

    # ------------------------------------------------------------------
    # Tick handling
    # ------------------------------------------------------------------

    def process_tick(self, tick: Tick) -> None:
        """Forward a tick to the underlying adapter (updates the
        latest-tick cache, fires subscribers, becomes the reference
        for any subsequent MARKET fill)."""
        self.adapter.process_tick(tick)

    def latest_tick(self, instrument_id: InstrumentId) -> Option[Tick]:
        """Best-known tick for ``instrument_id``; ``Nothing`` if the
        adapter has not seen one yet.

        Reads the adapter's private tick cache directly: the wrapper
        is co-developed with the adapter and lives in the same
        lifecycle phase. Adding a public accessor on the adapter
        would change its Protocol surface unnecessarily; this access
        is purely internal to ``backtesting/``.
        """
        tick = self.adapter._latest_tick.get(instrument_id)
        return Some(tick) if tick is not None else Nothing()

    # ------------------------------------------------------------------
    # Order submission — returns the resulting Trade directly
    # ------------------------------------------------------------------

    def submit(self, order: Order) -> Result[Trade, str]:
        """Submit an order and return the resulting ``Trade`` on
        success.

        Idempotency: re-submitting a previously-submitted order id
        succeeds at the adapter (returns the original ``OrderId``)
        but emits no new trade — the wrapper returns
        ``Err("backtest:no_trade_emitted")`` in that case to surface
        the (likely-buggy) duplicate submit at the call site.
        """
        n_before = len(self.adapter._trades)
        match self.adapter.submit(order):
            case Ok(_):
                trades_after = self.adapter._trades
                if len(trades_after) == n_before:
                    return Err(f"backtest:no_trade_emitted: order {order.id} produced no fill")
                return Ok(trades_after[-1])
            case Err(reason):
                return Err(reason)
