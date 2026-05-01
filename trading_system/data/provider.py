"""``MarketDataProvider`` Protocol — adapter contract for market data.

Every concrete provider (mock, broker feed, CSV replay, exchange API)
implements this Protocol; engine and strategy modules depend only on
it (REQ_SDS_INT_002, REQ_F_BRK_005-equivalent for data).

Methods return ``Result[T, str]`` with category-prefixed error strings
per REQ_SDD_ERR_002. Conventional prefixes:

- ``data:not_found``       — instrument / fundamentals / dividend missing
- ``data:invalid_range``   — start after end, etc.
- ``data:no_as_of``        — ``latest`` requires the provider's "now"
                             to be configured first
- ``data:corrupted``       — out-of-order or malformed feed
                             (caller SHALL trip an EXECUTION kill-switch
                             trigger per REQ_S_KS_005, REQ_SDD_API_007)
- ``network:<reason>``     — transport-level failures in live providers

REQ refs:
- REQ_SDS_INT_002 — interface methods (``bars`` / ``latest`` /
  ``dividends`` / ``fundamentals``).
- REQ_SDD_API_002 — declared as ``@runtime_checkable`` Protocol so that
  ``isinstance`` checks at adapter boundaries succeed.
- REQ_SDD_API_007 — ``bars`` returns strictly ascending ``at`` order.
- REQ_F_BCT_001 — feed must be deterministic (mock implementation).
- REQ_F_BCT_005 — dividends consumed by the dividend simulator.
- REQ_F_SCR_001 — fundamentals consumed by the screener.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from trading_system.data.types import Bar, Fundamentals, Timeframe
from trading_system.models.instrument import Instrument
from trading_system.models.trading import Dividend
from trading_system.result import Result


@runtime_checkable
class MarketDataProvider(Protocol):
    """Read-only market-data contract.

    All methods are pure-query: no side effects, no caching guarantees,
    no observable state changes between calls. Concrete implementations
    MAY cache internally as long as cache invalidation does not produce
    different return values for identical inputs.
    """

    def bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Result[list[Bar], str]:
        """Return OHLCV bars for ``instrument`` at ``timeframe`` between
        ``start`` and ``end`` (inclusive). The returned list MUST be
        sorted strictly ascending by ``Bar.at`` (REQ_SDD_API_007); an
        empty range returns ``Ok([])``."""
        ...

    def latest(self, instrument: Instrument) -> Result[Bar, str]:
        """Return the most recent bar for ``instrument`` as of the
        provider's configured "now"."""
        ...

    def dividends(self, instrument: Instrument, year: int) -> Result[list[Dividend], str]:
        """Return all dividends paid (or scheduled) on ``instrument``
        for the given ``year``. Empty list ⇒ no events."""
        ...

    def fundamentals(self, instrument: Instrument) -> Result[Fundamentals, str]:
        """Return the latest fundamentals snapshot for ``instrument``.
        ``Err("data:not_found: ...")`` when the instrument is unknown."""
        ...
