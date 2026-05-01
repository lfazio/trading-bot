"""Market-data layer (L2 adapter).

Defines the ``MarketDataProvider`` Protocol and ships a deterministic
in-process ``MockMarketDataProvider``. Concrete live providers (broker
feeds, exchange APIs, vendor CSVs) plug in by implementing the same
Protocol; the engine layer never imports a concrete provider.

REQ refs:
- REQ_SDS_INT_002 — Protocol surface (``bars`` / ``latest`` /
  ``dividends`` / ``fundamentals``).
- REQ_SDD_API_002 — adapter contracts declared as runtime-checkable
  ``typing.Protocol``.
- REQ_SDD_API_007 — ``bars`` returns strictly ascending ``at`` order.
- REQ_SDD_TST_002 — mock produces identical bar series for identical
  (seed, instrument, timeframe, range) tuples.
- REQ_F_BCT_001 — deterministic feed underpins reproducible backtests.
- REQ_F_SCR_001 / REQ_F_BCT_005 — ``Fundamentals`` and ``Dividend``
  shapes consumed by the screener and dividend simulator.
"""

from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar, Fundamentals, Timeframe

__all__ = [
    "Bar",
    "Fundamentals",
    "MarketDataProvider",
    "MockMarketDataProvider",
    "Timeframe",
]
