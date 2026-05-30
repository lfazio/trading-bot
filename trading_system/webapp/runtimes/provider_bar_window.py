"""Pull a recent-bars window from a ``MarketDataProvider``.

Lives under ``webapp/runtimes/`` (not ``webapp/``) so the
structural audit's view-tier ban on ``trading_system.data.*``
imports stays intact; ``runtimes/`` is the documented carve-out
for engine-layer reach.

Pure function: takes the provider + instrument, returns a list
of (closes, timestamps) for the trailing window. Catches every
exception + returns empty lists on failure so the dashboard
panel never crashes the SSE channel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.models.instrument import Instrument


def fetch_recent_close_window(
    provider: MarketDataProvider,
    instrument: Instrument,
    *,
    days: int = 120,
) -> tuple[list[Decimal], list[datetime]]:
    """Return ``(closes, timestamps)`` for the last ``days`` of
    daily bars. Both lists are empty on any failure (cache miss
    + offline, provider unreachable, etc.) — the dashboard
    panel renders the empty-state placeholder."""
    try:
        end = datetime.now(tz=UTC)
        window_start = end - timedelta(days=days)
        result = provider.bars(instrument, Timeframe.D1, window_start, end)
    except Exception:  # noqa: BLE001 — defensive
        return [], []
    if not hasattr(result, "is_ok") or not result.is_ok():
        return [], []
    bars = result.unwrap()
    closes = [b.close for b in bars]
    timestamps = [b.at for b in bars]
    return closes, timestamps


def fetch_volatility_index_window(
    provider: MarketDataProvider,
    *,
    symbol: str,
    days: int = 120,
) -> tuple[str, list[Decimal], list[datetime]]:
    """CR-026 follow-up — fetch the implied-vol benchmark series
    (``^VIX`` or ``^VSTOXX``) for the configured symbol. Returns
    ``(symbol, closes, timestamps)``; empty closes/timestamps on
    any failure. The Stock-shell synthesis matches the CR-009
    yfinance index convention so the CR-021 envelope cache key
    is consistent across the dashboard's reference-index surface
    and the VIX overlay."""
    from trading_system.models.identifiers import InstrumentId
    from trading_system.models.instrument import InstrumentClass, Stock
    from trading_system.models.money import Currency

    currency = Currency.USD if symbol == "^VIX" else Currency.EUR
    country = "US" if symbol == "^VIX" else "EU"
    vix_stock = Stock(
        id=InstrumentId(symbol),
        symbol=symbol,
        exchange="INDEX",
        currency=currency,
        cls=InstrumentClass.STOCK,
        isin=f"INDEX_{symbol.lstrip('^')}",
        sector="volatility_index",
        country=country,
    )
    closes, timestamps = fetch_recent_close_window(
        provider, vix_stock, days=days
    )
    return symbol, closes, timestamps


def fetch_recent_bar_window(
    provider: MarketDataProvider,
    instrument: Instrument,
    *,
    days: int = 120,
) -> tuple[list[Decimal], list[datetime], list[Decimal]]:
    """CR-026 follow-up — same shape as
    :func:`fetch_recent_close_window` but also returns the
    parallel ``volume`` series so the dashboard can render
    a volume strip below the price line. Returns
    ``([], [], [])`` on any failure."""
    try:
        end = datetime.now(tz=UTC)
        window_start = end - timedelta(days=days)
        result = provider.bars(instrument, Timeframe.D1, window_start, end)
    except Exception:  # noqa: BLE001 — defensive
        return [], [], []
    if not hasattr(result, "is_ok") or not result.is_ok():
        return [], [], []
    bars = result.unwrap()
    closes = [b.close for b in bars]
    timestamps = [b.at for b in bars]
    volumes = [b.volume for b in bars]
    return closes, timestamps, volumes
