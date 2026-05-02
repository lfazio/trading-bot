"""``BrokerAdapter`` Protocol — the broker-agnostic execution surface.

Engine and strategy modules depend on this Protocol only; concrete
adapters (``LocalBrokerAdapter`` and any future live adapter) implement
it (REQ_F_BRK_001, REQ_F_BRK_005).

Methods return ``Result[T, str]`` with category-prefixed errors per
REQ_SDD_ERR_002. Conventional prefixes:

- ``broker:rejected``        — the broker refused the order
- ``broker:not_found``       — unknown order id / symbol
- ``broker:already_filled``  — cannot cancel a filled order
- ``broker:no_market_data``  — submit() requires a recent tick
- ``broker:order_unsupported`` — order type not yet implemented
- ``network:<reason>``       — transport-level (live adapters only)

REQ refs:
- REQ_F_BRK_001 — full surface (submit / cancel / positions /
  account_state / instrument / subscribe).
- REQ_F_BRK_002 — ``LocalBrokerAdapter`` is the only concrete adapter
  shipped through the lifecycle.
- REQ_SDS_INT_001 — Protocol surface; conformance test pattern.
- REQ_SDD_API_002 — runtime-checkable Protocol.
- REQ_SDD_API_006 — submit / cancel idempotent on duplicate client ids.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from trading_system.execution.types import Account, Tick
from trading_system.models.identifiers import OrderId
from trading_system.models.instrument import Instrument
from trading_system.models.trading import Order, Position
from trading_system.result import Option, Result


@runtime_checkable
class Subscription(Protocol):
    """A handle returned by ``BrokerAdapter.subscribe``. Calling
    ``cancel`` stops further callbacks; subsequent calls are no-ops."""

    def cancel(self) -> None: ...


@runtime_checkable
class BrokerAdapter(Protocol):
    """Read-write broker contract.

    Implementations SHALL be deterministic for identical inputs when
    seeded (REQ_SDS_ARC_005). The shared conformance test suite
    (``tests/execution/test_conformance.py``, REQ_SDD_TST_001) runs
    parametrized over every concrete adapter.
    """

    def submit(self, order: Order) -> Result[OrderId, str]:
        """Submit an order. Returns the broker-side ``OrderId`` on
        success, or an ``Err`` with a category prefix. Re-submitting
        with the same client-side id is idempotent: the original
        ``OrderId`` is returned."""
        ...

    def cancel(self, order_id: OrderId) -> Result[bool, str]:
        """Cancel a pending order. ``Ok(True)`` if cancelled,
        ``Err("broker:already_filled" | "broker:not_found" | ...)``
        otherwise. Re-cancelling a cancelled order returns
        ``Ok(False)`` (idempotent)."""
        ...

    def positions(self) -> list[Position]:
        """Return all currently-open positions."""
        ...

    def account_state(self) -> Account:
        """Return a snapshot of cash, realized PnL, unrealized PnL,
        and equity."""
        ...

    def instrument(self, symbol: str) -> Option[Instrument]:
        """Look up an instrument by symbol. Returns ``Some`` when
        known to the adapter, ``Nothing`` otherwise. Callers SHALL
        NOT assume that successful lookup implies tradeability."""
        ...

    def subscribe(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> Subscription:
        """Register a tick callback for the given symbols. Returns a
        ``Subscription`` whose ``cancel`` stops further callbacks."""
        ...
