"""Fee models — broker-cost simulation primitives.

The ``FeeModel`` Protocol is consumed by both ``LocalBrokerAdapter``
(REQ_F_BRK_002) and the backtesting engine (REQ_F_BCT_002). The
default implementation, ``FlatFeeModel``, charges a fixed commission
plus a notional-proportional spread cost (a stand-in for the
broker-side spread that simulations cannot observe directly).

REQ refs: REQ_F_BCT_002, REQ_SDD_TYP_001, REQ_SDS_ARC_002 (pure
function — no I/O, no module-level state).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.models.money import Money
from trading_system.models.trading import Order

_BPS = Decimal(10_000)


@runtime_checkable
class FeeModel(Protocol):
    """Compute the realized broker fee for a fill.

    ``fill_price`` is the price at which the order actually fills,
    after slippage. The returned ``Money`` is non-negative and shares
    the order's instrument currency.
    """

    def fees(self, order: Order, fill_price: Decimal) -> Money: ...


@dataclass(frozen=True, slots=True)
class FlatFeeModel:
    """Fixed commission per trade + spread cost in basis points.

    ``commission`` is added to the spread-driven cost; both contribute
    to the returned fee. ``spread_bps`` of zero gives a pure-commission
    model; ``commission`` of zero gives a pure-bps model.
    """

    commission: Money
    spread_bps: Decimal

    def __post_init__(self) -> None:
        if self.commission.amount < 0:
            raise ValueError(f"FlatFeeModel.commission must be >= 0, got {self.commission.amount}")
        if self.spread_bps < 0:
            raise ValueError(f"FlatFeeModel.spread_bps must be >= 0, got {self.spread_bps}")

    def fees(self, order: Order, fill_price: Decimal) -> Money:
        currency = order.instrument.currency
        assert self.commission.currency == currency, (
            f"FlatFeeModel.commission currency {self.commission.currency} "
            f"!= order currency {currency}"
        )
        notional = order.quantity * fill_price
        spread_cost = (notional * self.spread_bps) / _BPS
        return self.commission + Money(spread_cost, currency)
