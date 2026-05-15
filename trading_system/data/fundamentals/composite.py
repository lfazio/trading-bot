"""``CompositeFundamentalsProvider`` — chain of
``MarketDataProvider`` delegates.

Each Protocol method tries the delegates in registration order and
returns the first ``Ok`` it sees. If every delegate returns ``Err``,
the composite returns the LAST ``Err`` so the operator sees the
most-specific reason (REQ_F_FND_004 / REQ_SDD_FND_003). An empty
composite (no delegates) surfaces ``Err("data:not_supported:
composite_empty")`` rather than panicking — operators sometimes build
the composite from a config-loaded list that could legitimately be
empty.

REQ refs: REQ_F_FND_004, REQ_SDS_FND_001, REQ_SDD_FND_003.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar, Fundamentals, Timeframe
from trading_system.models.instrument import Instrument
from trading_system.models.trading import Dividend
from trading_system.result import Err, Ok, Result

_EMPTY_COMPOSITE = "data:not_supported:composite_empty"


@dataclass(slots=True)
class CompositeFundamentalsProvider:
    """Read-only composition of multiple ``MarketDataProvider`` delegates.

    The delegate tuple is immutable — operators build the composite
    once at startup. Adding a delegate is a new composite, not a
    mutation.
    """

    delegates: tuple[MarketDataProvider, ...]

    def fundamentals(self, instrument: Instrument) -> Result[Fundamentals, str]:
        return self._first_ok_last_err(
            lambda d: d.fundamentals(instrument)
        )

    def bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Result[list[Bar], str]:
        return self._first_ok_last_err(
            lambda d: d.bars(instrument, timeframe, start, end)
        )

    def latest(self, instrument: Instrument) -> Result[Bar, str]:
        return self._first_ok_last_err(lambda d: d.latest(instrument))

    def dividends(
        self, instrument: Instrument, year: int
    ) -> Result[list[Dividend], str]:
        return self._first_ok_last_err(
            lambda d: d.dividends(instrument, year)
        )

    # ------------------------------------------------------------------
    # First-Ok / last-Err pattern
    # ------------------------------------------------------------------

    def _first_ok_last_err(self, call):  # type: ignore[no-untyped-def]
        if not self.delegates:
            return Err(_EMPTY_COMPOSITE)
        last_err: str = ""
        for d in self.delegates:
            match call(d):
                case Ok(value):
                    return Ok(value)
                case Err(reason):
                    last_err = reason
        return Err(last_err)
