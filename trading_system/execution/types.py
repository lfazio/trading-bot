"""Execution-layer data types: ``Tick`` and ``Account``.

REQ refs: REQ_SDD_TYP_001 / 002 / 003, REQ_F_BRK_001 (account_state
return type).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.models.identifiers import InstrumentId
from trading_system.models.money import Money


@dataclass(frozen=True, slots=True)
class Tick:
    """A single market-data tick used to drive the local simulator.

    ``bid`` and ``ask`` are the best two-sided prices; ``last`` is the
    most recent traded print. Invariants:

    - all three prices > 0,
    - ``bid <= last <= ask``,
    - ``volume >= 0``.
    """

    at: datetime
    instrument_id: InstrumentId
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.bid <= 0:
            raise ValueError(f"Tick.bid must be > 0, got {self.bid}")
        if self.ask <= 0:
            raise ValueError(f"Tick.ask must be > 0, got {self.ask}")
        if self.last <= 0:
            raise ValueError(f"Tick.last must be > 0, got {self.last}")
        if self.bid > self.ask:
            raise ValueError(f"Tick.bid ({self.bid}) must be <= ask ({self.ask})")
        if not (self.bid <= self.last <= self.ask):
            raise ValueError(
                f"Tick.last ({self.last}) must lie in [bid={self.bid}, ask={self.ask}]"
            )
        if self.volume < 0:
            raise ValueError(f"Tick.volume must be >= 0, got {self.volume}")


@dataclass(frozen=True, slots=True)
class Account:
    """Broker-side account snapshot (gross — tax is applied by
    ``portfolio/`` at realization, REQ_F_PRT_001).

    Invariants:

    - all monetary fields share a currency,
    - ``equity == cash + cost_basis + unrealized_pnl``
      (the broker carries the running value of open positions; the
      caller can reconcile via this identity).
    """

    cash: Money
    realized_pnl: Money
    unrealized_pnl: Money
    equity: Money

    def __post_init__(self) -> None:
        currency = self.cash.currency
        for fld in (self.realized_pnl, self.unrealized_pnl, self.equity):
            if fld.currency != currency:
                raise ValueError(
                    f"Account fields must share a currency; expected {currency}, got {fld.currency}"
                )
