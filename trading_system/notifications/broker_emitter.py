"""``NotifyingBrokerWrapper`` — wraps any ``BrokerAdapter`` and
emits an ``AnomalyAlert`` on every categorised-Err return.

REQ refs:
- REQ_F_NOT_007 — AnomalyAlert payloads for events short of a KS
  trip; broker rejections are the canonical example.
- REQ_SDD_NOT_006 — emitters live with their upstream subsystem.
  The wrapper is the seam between the broker subsystem (which
  produces categorised Errs) and the notification fan-out (which
  delivers them); both surfaces stay free of cross-imports.

The wrapper is opt-in — production deployments construct a
``NotifyingBrokerWrapper(broker, emitter)`` instead of the bare
``LocalBrokerAdapter``. Backtest + single-account demos pass the
bare adapter (no wrapper, no fan-out, no emission) so REQ_NF_NOT_001
(notifications never block the trade-execution critical path) and
REQ_NF_ACC_001 (legacy single-account backwards compat) hold
bit-identically.

The wrapper preserves the full ``BrokerAdapter`` Protocol — every
method delegates to the wrapped adapter, intercepts the Result, and
fires the emitter only on Err. The wrapped adapter's identity is
held by reference; ``isinstance`` checks against ``BrokerAdapter``
SHALL still pass.

**Import-graph discipline (REQ_NF_NOT_001 + the notifications
structural test):** this module SHALL NOT import from
``trading_system.execution`` even though it logically wraps a
``BrokerAdapter``. The inner adapter is typed as ``Any`` so the
wrapper stays Protocol-agnostic; Python's duck typing carries the
actual concrete types through every method delegation. The
``Order`` / ``OrderId`` parameter types are kept for clarity but
their imports stay rooted at ``models/`` (L1), which the
notifications package may import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_system.models.identifiers import OrderId
from trading_system.models.trading import Order
from trading_system.notifications.emitters import (
    AnomalyEmitter,
    emit_broker_rejection,
)
from trading_system.result import Err, Result


@dataclass(slots=True)
class NotifyingBrokerWrapper:
    """Decorator that emits an AnomalyAlert on every broker-side
    rejection.

    The wrapped adapter does the actual work; the wrapper's only job
    is to fan the Err's categorised reason through the emitter. Order
    submission stays synchronous; the fan-out dispatch is fire-and-
    forget (the emitter's ``dispatch`` returns immediately under the
    ``NotificationFanOut`` Protocol — REQ_NF_NOT_001 invariant).
    """

    inner: Any  # BrokerAdapter Protocol; typed Any to keep the
    # notifications package free of an `execution/` import.
    emitter: AnomalyEmitter | None = None

    # ------------------------------------------------------------------
    # BrokerAdapter delegation — duck-typed to the inner adapter.
    # ------------------------------------------------------------------

    def submit(self, order: Order) -> Result[OrderId, str]:
        result = self.inner.submit(order)
        if isinstance(result, Err):
            emit_broker_rejection(
                self.emitter,
                reason=result.error,
                detail=f"order {order.id} on {order.instrument.id}",
            )
        return result

    def cancel(self, order_id: OrderId) -> Result[bool, str]:
        result = self.inner.cancel(order_id)
        if isinstance(result, Err):
            emit_broker_rejection(
                self.emitter,
                reason=result.error,
                detail=f"cancel {order_id}",
            )
        return result

    def positions(self) -> Any:
        return self.inner.positions()

    def account_state(self) -> Any:
        return self.inner.account_state()

    def instrument(self, symbol: str) -> Any:
        return self.inner.instrument(symbol)

    def subscribe(self, symbols: list[str], on_tick: Any) -> Any:
        return self.inner.subscribe(symbols, on_tick)


__all__ = ["NotifyingBrokerWrapper"]
