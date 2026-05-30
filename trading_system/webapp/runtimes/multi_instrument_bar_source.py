"""CR-026 — multi-instrument bar fan-out for paper-trading runtimes.

``MultiInstrumentBarSource`` wraps a ``MarketDataProvider`` (typically
``YFinanceMarketDataProvider``) and exposes a single ``poll()`` call
that returns the latest bar for every instrument in the configured
universe. Iteration order is lex-sorted by symbol — replay
byte-equality (REQ_NF_DAT_001) extends to multi-instrument sessions.

REQ refs:
- REQ_F_PAP_016 — universe-wide fan-out with deterministic order.
- REQ_SDD_PAP_008 — partial-fan-out graceful-degrade contract:
  ``Err("data:no_bars")`` only when EVERY symbol fails; any
  successful subset returns ``Ok({...})``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import Stock
from trading_system.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class MultiInstrumentBarSource:
    """REQ_F_PAP_016 / REQ_SDD_PAP_008 — universe-wide bar fan-out.

    The source SHALL iterate the configured universe in lex-sorted
    symbol order. The wrapped ``MarketDataProvider`` is queried
    per-symbol; v1 ships per-symbol ``latest(instrument)`` calls.
    A future CR may batch these into a single
    ``yfinance.download(symbols=[…])`` round-trip if measured load
    proves it worth the additional code path.
    """

    universe: tuple[Stock, ...]
    provider: MarketDataProvider

    def __post_init__(self) -> None:
        if not self.universe:
            raise ValueError(
                "MultiInstrumentBarSource.universe must contain at least one stock"
            )
        ordered = tuple(sorted(self.universe, key=lambda s: s.symbol))
        object.__setattr__(self, "universe", ordered)

    def poll(self) -> Result[Mapping[InstrumentId, Bar], str]:
        """REQ_SDD_PAP_008 — fetch the latest bar for every universe
        instrument.

        Returns:
          - ``Ok({instrument_id: bar, ...})`` — at least one
            symbol's bar was retrieved. Insertion order is
            lex-sorted by symbol (Python preserves dict insertion
            order ⇒ deterministic iteration).
          - ``Err("data:no_bars")`` — every symbol's
            ``provider.latest(...)`` returned ``Err``. The runtime
            falls back to its CR-021 envelope-cache path.
        """
        result: dict[InstrumentId, Bar] = {}
        for stock in self.universe:
            outcome = self.provider.latest(stock)
            if isinstance(outcome, Ok):
                result[stock.id] = outcome.value
        if not result:
            return Err("data:no_bars")
        return Ok(result)
