"""Implied-volatility benchmark provider (CR-028 — REQ_F_IND_005).

The VIX (`^VIX`) and VSTOXX (`^VSTOXX`) are the canonical
implied-volatility benchmarks for US and EU markets. Strategies
that consume volatility regime gates read them through this
Protocol — the existing CR-009 `YFinanceMarketDataProvider` handles
the bar fetch + the CR-021 envelope cache handles replay
determinism (REQ_NF_DAT_001).

v1 SHALL NOT synthesise implied vol from option chains; the
published index is the system of record.

REQ refs:
- REQ_F_IND_005 — Protocol + concrete adapter.
- REQ_SDD_IND_004 — symbol registry + Err category.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err, Result


@runtime_checkable
class VolatilityIndexProvider(Protocol):
    """REQ_F_IND_005 — read-only `latest(symbol)` accessor for an
    implied-volatility benchmark index."""

    def latest(self, symbol: str) -> Result[Bar, str]: ...


# Built-in symbol registry. Adding a new index is a code change —
# the registry is intentionally small + closed so the operator can't
# accidentally point the surface at a non-volatility instrument
# (REQ_SDD_IND_004).
_KNOWN_INDICES: dict[str, Currency] = {
    "^VIX": Currency.USD,
    "^VSTOXX": Currency.EUR,
}


@dataclass(slots=True)
class YFinanceVolatilityIndexProvider:
    """REQ_F_IND_005 / REQ_SDD_IND_004 — concrete adapter wrapping
    the operator-configured ``MarketDataProvider``.

    Lookups for unknown symbols surface
    ``Err("volatility_index:unknown_symbol:<symbol>")`` BEFORE the
    wrapped provider is consulted — the closed registry is the
    operator-facing boundary so a typoed symbol fails fast with a
    categorised reason.
    """

    provider: MarketDataProvider

    def latest(self, symbol: str) -> Result[Bar, str]:
        currency = _KNOWN_INDICES.get(symbol)
        if currency is None:
            return Err(f"volatility_index:unknown_symbol:{symbol}")
        instrument = _build_index_stock(symbol, currency)
        return self.provider.latest(instrument)


def _build_index_stock(symbol: str, currency: Currency) -> Stock:
    """Build a ``Stock`` shell for the index. Matches the
    ``index_for_universe`` convention (REQ_F_WEB2_010 follow-on) so
    the cache key is consistent with the dashboard's reference-index
    surface."""
    domain_id = symbol.lstrip("^") or symbol
    return Stock(
        id=InstrumentId(symbol),
        symbol=symbol,
        exchange="INDEX",
        currency=currency,
        cls=InstrumentClass.STOCK,
        isin=f"INDEX_{domain_id}",
        sector="volatility_index",
        country="US" if currency == Currency.USD else "EU",
    )
