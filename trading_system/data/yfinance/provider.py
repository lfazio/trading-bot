"""``YFinanceMarketDataProvider`` — backtest historical-data adapter.

Cache-first concrete ``MarketDataProvider``. The adapter:

- reads from ``YFinanceCache`` on every call;
- on cache miss, returns ``Err("data:cache_miss_offline:...")``
  unless ``allow_network=True``;
- when network is allowed, calls a lazily-imported ``yfinance``
  download with retry + exponential backoff on transient errors,
  maps the result to our domain types, persists to the cache, and
  returns;
- panics at construction if ``run_mode == "live"`` — yfinance is an
  unofficial Yahoo scraper and SHALL NOT drive live decisions.

The runtime / test environment imports nothing from ``yfinance`` or
``pandas`` until the first cache miss with network enabled. Tests
substitute a fake ``downloader`` callable and never trigger the real
import.

REQ refs: REQ_F_DAT_001, REQ_F_DAT_005, REQ_F_DAT_006, REQ_F_DAT_009,
REQ_F_DAT_010, REQ_NF_DAT_001, REQ_SDS_DAT_001, REQ_SDS_DAT_002,
REQ_SDS_DAT_004, REQ_SDD_DAT_012, REQ_SDD_DAT_013, REQ_SDD_ERR_005.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trading_system.data.types import Bar, Fundamentals, Timeframe
from trading_system.data.yfinance.cache import CacheKey, YFinanceCache
from trading_system.data.yfinance.mappers import bars_from_yf, dividends_from_yf
from trading_system.data.yfinance.symbols import yahoo_symbol_for
from trading_system.models.instrument import Instrument
from trading_system.models.money import Currency
from trading_system.models.trading import Dividend
from trading_system.result import Err, Nothing, Ok, Result, Some

# A downloader takes (symbol, timeframe_value, start, end) and returns
# either the OHLCV table (for bars) or an iterable of (timestamp,
# amount) pairs (for dividends). Tests inject a fake; production
# imports the real yfinance lazily.
BarDownloader = Callable[[str, str, datetime, datetime], Any]
DividendDownloader = Callable[[str, int], Any]

# Retry policy (REQ_SDD_DAT_012 / REQ_SDD_ERR_005): up to 3 attempts
# with exponential backoff on transient errors. Backoff base in
# seconds; tests that exercise the retry path inject a backoff_sleep
# stub to keep the suite fast.
_RETRY_LIMIT = 3
_RETRY_BACKOFF_BASE = 0.5

# Transient error category prefixes (REQ_SDD_DAT_012). The retry loop
# kicks in only when the inner call raises one of these *as a
# string-prefixed signal*; raised exceptions from the network layer
# are translated into these prefixes by the lazy yfinance integration
# in ``_default_bar_downloader``.
_TRANSIENT_PREFIXES = ("data:rate_limited", "data:network")


class TransientDownloadError(Exception):
    """Raised by a downloader to signal a retryable transient error.

    The message SHALL start with one of ``data:rate_limited`` or
    ``data:network`` to thread cleanly into ``REQ_SDD_DAT_012``.
    """


@dataclass(slots=True)
class YFinanceMarketDataProvider:
    """Backtest-only concrete ``MarketDataProvider``.

    Construction parameters:
    - ``cache`` — the ``YFinanceCache`` instance backing the
      replay-deterministic store (REQ_NF_DAT_001).
    - ``currency`` — used to attach Money denominations on dividend
      events; we accept it explicitly rather than reading from
      yfinance because yfinance's metadata is unreliable.
    - ``allow_network`` — default ``False``; CI / replay runs SHALL
      run with this off (REQ_F_DAT_006).
    - ``run_mode`` — default ``"backtest"``; setting ``"live"``
      panics at ``__post_init__`` (REQ_F_DAT_009 / REQ_SDS_DAT_004).
    - ``bar_downloader`` / ``dividend_downloader`` — callables that
      fetch from yfinance. Defaults import yfinance lazily and call
      it only when invoked. Tests inject a fake.
    - ``backoff_sleep`` — sleep function used by the retry loop;
      tests inject a no-op.
    """

    cache: YFinanceCache
    currency: Currency
    allow_network: bool = False
    run_mode: str = "backtest"
    bar_downloader: BarDownloader | None = None
    dividend_downloader: DividendDownloader | None = None
    backoff_sleep: Callable[[float], None] = field(default=time.sleep)

    def __post_init__(self) -> None:
        if self.run_mode == "live":
            raise RuntimeError(
                "YFinanceMarketDataProvider forbidden in live mode "
                "(REQ_F_DAT_009 / REQ_SDS_DAT_004): yfinance is an "
                "unofficial scraper and SHALL NOT drive live decisions"
            )
        if self.bar_downloader is None:
            self.bar_downloader = _default_bar_downloader
        if self.dividend_downloader is None:
            self.dividend_downloader = _default_dividend_downloader

    # ------------------------------------------------------------------
    # MarketDataProvider Protocol
    # ------------------------------------------------------------------

    def bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Result[list[Bar], str]:
        sym_res = yahoo_symbol_for(instrument)
        if isinstance(sym_res, Err):
            return Err(sym_res.error)
        sym = sym_res.value
        key = CacheKey(symbol=sym, timeframe=timeframe.value, start=start, end=end)
        match self.cache.get_bars(key):
            case Some(bars):
                return Ok(bars)
            case Nothing():
                pass
        if not self.allow_network:
            return Err(f"data:cache_miss_offline:{sym}")
        return self._download_bars(instrument, timeframe, start, end, key, sym)

    def dividends(
        self,
        instrument: Instrument,
        year: int,
    ) -> Result[list[Dividend], str]:
        sym_res = yahoo_symbol_for(instrument)
        if isinstance(sym_res, Err):
            return Err(sym_res.error)
        sym = sym_res.value
        match self.cache.get_dividends(sym, year, self.currency):
            case Some(divs):
                return Ok(divs)
            case Nothing():
                pass
        if not self.allow_network:
            return Err(f"data:cache_miss_offline:{sym}:dividends:{year}")
        return self._download_dividends(instrument, year, sym)

    def latest(self, instrument: Instrument) -> Result[Bar, str]:
        # REQ_SDD_DAT_013: latest() is offline-only; no network fetch
        # regardless of allow_network. Returns the most recent bar
        # already cached for the instrument.
        sym_res = yahoo_symbol_for(instrument)
        if isinstance(sym_res, Err):
            return Err(sym_res.error)
        sym = sym_res.value
        # Walk every cached file under the symbol and pick the bar
        # with the latest timestamp. v1 implementation keeps it simple;
        # SQLite migration (CR-008) will replace with an indexed query.
        latest_bar = _scan_latest_cached_bar(self.cache, sym)
        if latest_bar is None:
            return Err(f"data:not_found:{sym}:latest")
        return Ok(latest_bar)

    def fundamentals(self, instrument: Instrument) -> Result[Fundamentals, str]:
        # REQ_F_DAT_010: fundamentals NOT sourced from yfinance.
        _ = instrument
        return Err("data:not_supported:fundamentals_via_yfinance")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _download_bars(  # noqa: PLR0913 - mirrors `bars()` Protocol surface
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        key: CacheKey,
        symbol: str,
    ) -> Result[list[Bar], str]:
        download_res = self._call_with_retry(
            lambda: _safe_call(self.bar_downloader, symbol, timeframe.value, start, end)
        )
        if isinstance(download_res, Err):
            return Err(download_res.error)
        df = download_res.value
        if df is None or _empty(df):
            return Err(f"data:not_found:{symbol}")
        bars = bars_from_yf(df, instrument)
        # Persist before return so the next call reads from cache
        # (REQ_F_DAT_004); a put failure is logged via the returned
        # Err but does not invalidate the just-fetched bars.
        put_res = self.cache.put_bars(key, bars)
        if isinstance(put_res, Err):
            return Err(put_res.error)
        return Ok(bars)

    def _download_dividends(
        self,
        instrument: Instrument,
        year: int,
        symbol: str,
    ) -> Result[list[Dividend], str]:
        download_res = self._call_with_retry(
            lambda: _safe_call(self.dividend_downloader, symbol, year)
        )
        if isinstance(download_res, Err):
            return Err(download_res.error)
        series = download_res.value
        if series is None:
            return Err(f"data:not_found:{symbol}:dividends:{year}")
        divs = dividends_from_yf(series, instrument, self.currency)
        put_res = self.cache.put_dividends(symbol, year, divs)
        if isinstance(put_res, Err):
            return Err(put_res.error)
        return Ok(divs)

    def _call_with_retry(self, fn: Callable[[], Any]) -> Result[Any, str]:
        """Run ``fn`` with up to ``_RETRY_LIMIT`` attempts; transient
        errors retry with exponential backoff, terminal errors
        propagate immediately."""
        last_reason: str | None = None
        for attempt in range(1, _RETRY_LIMIT + 1):
            try:
                return Ok(fn())
            except TransientDownloadError as e:
                last_reason = str(e)
                if not _is_transient(last_reason):
                    return Err(last_reason)
                if attempt < _RETRY_LIMIT:
                    self.backoff_sleep(_RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
        # All attempts exhausted on transient errors.
        return Err(last_reason or "data:network:unknown")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _safe_call(fn: Any, *args: Any) -> Any:
    """Invoke ``fn(*args)`` — kept tiny so the lambda in
    ``_call_with_retry`` doesn't reach for closure attrs."""
    return fn(*args)


def _is_transient(reason: str) -> bool:
    return any(reason.startswith(p) for p in _TRANSIENT_PREFIXES)


def _empty(df: Any) -> bool:
    """Cheap truthiness for pandas-shaped results without importing
    pandas. yfinance returns objects with an ``.empty`` attribute on
    DataFrame; tests pass plain lists where ``len() == 0`` works."""
    if hasattr(df, "empty"):
        return bool(df.empty)
    try:
        return len(df) == 0
    except TypeError:
        return False


def _scan_latest_cached_bar(cache: YFinanceCache, symbol: str) -> Bar | None:
    """Walk every bars file under ``<root>/<symbol>/`` and return the
    newest bar by ``Bar.at``. v1 implementation; SQLite migration
    replaces it with an indexed lookup."""
    safe = symbol.replace("/", "_")
    sym_dir = cache.root / safe
    if not sym_dir.exists():
        return None
    latest: Bar | None = None
    for path in sym_dir.rglob("*_bars.jsonl"):
        # We don't have the original CacheKey, but _read_jsonl_bars
        # only needs the file path. Reach in deliberately — this
        # stays internal to data/yfinance/.
        bars_res = cache._read_jsonl_bars(path)
        if isinstance(bars_res, Err):
            continue
        for b in bars_res.value:
            if latest is None or b.at > latest.at:
                latest = b
    return latest


# ----------------------------------------------------------------------
# Default downloaders — lazy yfinance import (network branch only)
# ----------------------------------------------------------------------


def _default_bar_downloader(
    symbol: str,
    timeframe_value: str,
    start: datetime,
    end: datetime,
) -> Any:
    """Lazy-import yfinance and call ``download``.

    This is the ONLY path in the runtime that imports yfinance /
    pandas. It executes only when ``allow_network=True`` and a cache
    miss occurs — i.e., during the recorder script's bootstrap, not
    during normal backtests or CI.
    """
    yf = _import_yfinance()
    try:
        df = yf.download(
            symbol,
            interval=timeframe_value,
            start=start,
            end=end,
            progress=False,
            auto_adjust=False,  # REQ_F_DAT_008: keep raw Close
        )
    except Exception as e:
        raise TransientDownloadError(f"data:network:{e}") from e
    return df


def _default_dividend_downloader(symbol: str, year: int) -> Any:
    yf = _import_yfinance()
    try:
        ticker = yf.Ticker(symbol)
        series = ticker.dividends
    except Exception as e:
        raise TransientDownloadError(f"data:network:{e}") from e
    if series is None or len(series) == 0:
        return []
    # Filter to the requested calendar year and convert pandas
    # Series to a list[(datetime, amount)] so the rest of the code
    # never imports pandas.
    out = []
    for ts, amount in series.items():
        py_ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if py_ts.year == year:
            out.append((py_ts, amount))
    return out


def _import_yfinance() -> Any:
    """Lazy import; raises a categorised error if yfinance isn't
    installed (the optional ``[yfinance]`` extra). The import sits
    inside the function on purpose: top-level imports are forbidden
    here so the runtime / test environment never pulls yfinance
    until a network branch fires."""
    try:
        import yfinance as yf  # noqa: PLC0415 — see docstring
    except ImportError as e:
        raise TransientDownloadError(
            "data:network:yfinance_not_installed:install with `pip install trading-bot[yfinance]`"
        ) from e
    return yf
