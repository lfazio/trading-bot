#!/usr/bin/env python3
"""Bulk-cache a whole universe (plus its indices) from Yahoo Finance.

Loops over every stock in ``data/universes/<name>.yaml`` + the
declared ``indices:`` (e.g., ^FCHI for cac40) and persists daily
bars into the ``YFinanceCache``. Run once per refresh window; the
paper-trading runtime reads from the cache afterwards.

Usage::

    python tools/yfinance_recorder_universe.py \\
        --universe cac40 \\
        --start 2025-01-01 \\
        --end   2026-05-23 \\
        --cache-root var/yfinance-cache

The default cache root matches what the wizard's ``yfinance`` bar
source uses (``TRADING_BOT_YFINANCE_CACHE`` env var, fallback
``var/yfinance-cache``).

REQ refs: REQ_F_DAT_002, REQ_F_DAT_004 (cache as system of
record), REQ_NF_DAT_001 (replay determinism).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from trading_system.data.types import Timeframe
from trading_system.data.universes import load_universe
from trading_system.data.yfinance import YFinanceCache, YFinanceMarketDataProvider
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err, Ok


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Bulk-cache a universe's bars from Yahoo Finance "
            "(operator-run; populates YFinanceCache)."
        )
    )
    p.add_argument("--universe", required=True, help="Universe name (e.g., cac40)")
    p.add_argument(
        "--start",
        required=True,
        help="ISO-8601 date (e.g., 2025-01-01)",
    )
    p.add_argument(
        "--end",
        default=datetime.now(tz=UTC).date().isoformat(),
        help="ISO-8601 date (default: today)",
    )
    p.add_argument(
        "--timeframe",
        default="1d",
        choices=[t.value for t in Timeframe],
        help="Bar resolution (default: 1d)",
    )
    p.add_argument(
        "--cache-root",
        type=Path,
        default=Path("var") / "yfinance-cache",
        help="Cache directory (default: var/yfinance-cache)",
    )
    p.add_argument(
        "--include-dividends",
        action="store_true",
        help="Also fetch dividend events for every stock",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip symbols whose cache key already has bars",
    )
    p.add_argument(
        "--sleep-seconds",
        type=float,
        default=2.0,
        help=(
            "Seconds to sleep between symbol fetches. Yahoo Finance "
            "rate-limits aggressive scrapers (HTTP 429); a 2s delay "
            "across 40 symbols (~80 s total) stays well below the "
            "throttle. Set to 0 for back-to-back fetches when the "
            "operator has confirmed no rate-limit pressure."
        ),
    )
    p.add_argument(
        "--retry-on-rate-limit",
        type=int,
        default=3,
        help=(
            "Retry attempts per symbol when the upstream returns "
            "an empty DataFrame (typically the rate-limit signal). "
            "Each retry doubles the backoff."
        ),
    )
    return p.parse_args()


def _parse_date(s: str) -> datetime:
    dt = datetime.fromisoformat(s) if "T" in s else datetime.fromisoformat(s + "T00:00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _index_stock(index_id: str, currency: Currency) -> Stock:
    """Build a ``Stock`` for a ^FCHI-style index symbol so we can
    reuse the per-stock fetching path. ``InstrumentClass.STOCK``
    is a structural approximation — yfinance treats indices the
    same as stocks for ``download()`` purposes."""
    # Strip the leading caret for the domain id (NewType is just
    # a string); keep it for yahoo_symbol_for via the symbol field.
    domain_id = index_id.lstrip("^")
    return Stock(
        id=InstrumentId(index_id),
        symbol=index_id,
        exchange="INDEX",  # synthetic; yahoo_symbol_for has caret-handling
        currency=currency,
        cls=InstrumentClass.STOCK,
        isin=f"INDEX_{domain_id}",
        sector="index",
        country="FR",
    )


# Yahoo Finance enforces hard server-side limits on intraday
# timeframes — older bars simply aren't available + every fetch
# returns an empty DataFrame the provider categorises as
# data:not_found. The retry loop can't recover; reject the
# request up front with a useful message.
_INTRADAY_MAX_LOOKBACK_DAYS: dict[str, int] = {
    "1m": 7,    # documented hard cap (Yahoo gives ~7-8 days)
    "5m": 60,
    "15m": 60,
    "30m": 60,
    "1h": 60,   # actually 730d but conservative bound is 60
}


def main() -> int:
    args = _parse_args()
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if start >= end:
        print(f"start ({start}) must be < end ({end})", file=sys.stderr)
        return 2

    # Validate the date range against Yahoo's intraday-history cap
    # so the operator gets a clear error instead of 41 silent
    # "not_found" failures.
    cap_days = _INTRADAY_MAX_LOOKBACK_DAYS.get(args.timeframe)
    if cap_days is not None:
        from datetime import UTC as _UTC

        now = datetime.now(tz=_UTC)
        lookback_days = (now - start).days
        if lookback_days > cap_days:
            print(
                f"ERROR: --timeframe {args.timeframe} only has the last "
                f"~{cap_days} days of bars available from Yahoo Finance "
                f"(your --start is {lookback_days} days back). For "
                f"multi-year ranges use --timeframe 1d. To record the "
                f"intraday window, set --start {(now - __import__('datetime').timedelta(days=cap_days)).date()}.",
                file=sys.stderr,
            )
            return 2

    uni_res = load_universe(args.universe)
    if isinstance(uni_res, Err):
        print(f"universe load failed: {uni_res.error}", file=sys.stderr)
        return 2
    universe = uni_res.value
    print(
        f"recorder: universe {universe.name!r} "
        f"({len(universe.stocks)} stocks)",
        file=sys.stderr,
    )

    cache = YFinanceCache(root=args.cache_root)
    # The universe loader ignores the YAML's ``indices:`` key; we
    # re-read the file to grab them.
    import yaml as _yaml

    universe_root = Path(__file__).resolve().parent.parent / "data" / "universes"
    raw = _yaml.safe_load(
        (universe_root / f"{args.universe}.yaml").read_text(encoding="utf-8")
    )
    indices = raw.get("indices", []) or []

    timeframe = Timeframe(args.timeframe)
    args.cache_root.mkdir(parents=True, exist_ok=True)

    # One provider per currency keeps the cache-key + currency
    # invariants consistent. CAC 40 is EUR throughout.
    eur_provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=Currency.EUR,
        allow_network=True,
    )

    ok_count = 0
    err_count = 0
    skipped = 0

    import time as _time

    def _fetch(instrument: Stock) -> bool:
        nonlocal ok_count, err_count, skipped
        sym = str(instrument.id)
        if args.skip_existing:
            # Quick check — does the cache already have ANY bars
            # for this symbol/timeframe? Use the symbols.py helper
            # to mirror what the provider will look up.
            from trading_system.data.yfinance.cache import CacheKey

            key = CacheKey(
                symbol=instrument.symbol if sym.startswith("^") else sym,
                timeframe=timeframe.value,
                start=start,
                end=end,
            )
            existing = cache.get_bars(key)
            if hasattr(existing, "is_some") and existing.is_some():
                print(f"  {sym}: cached — skip", file=sys.stderr)
                skipped += 1
                return True
        print(
            f"  {sym}: fetching "
            f"{start.date()}..{end.date()} ({timeframe.value})",
            file=sys.stderr,
        )
        provider = eur_provider  # currency-agnostic for CAC40 (all EUR)

        # Retry loop — Yahoo's HTTP 429 surfaces as an empty
        # DataFrame which the provider categorises as
        # ``data:not_found``. Re-fetching after a backoff usually
        # works.
        bars_res = None
        backoff = max(1.0, args.sleep_seconds)
        for attempt in range(args.retry_on_rate_limit + 1):
            if attempt > 0:
                print(
                    f"  {sym}: retry {attempt}/{args.retry_on_rate_limit} "
                    f"after {backoff:.0f}s backoff",
                    file=sys.stderr,
                )
                _time.sleep(backoff)
                backoff *= 2
            bars_res = provider.bars(instrument, timeframe, start, end)
            if isinstance(bars_res, Ok):
                break
            # Only retry on the documented rate-limit / not_found
            # categories; surface other Errs immediately.
            err = bars_res.error
            if not (
                "not_found" in err
                or "rate_limited" in err
                or "network" in err
            ):
                break

        if isinstance(bars_res, Err) or bars_res is None:
            reason = bars_res.error if bars_res is not None else "no_result"
            print(f"  {sym}: ERROR {reason}", file=sys.stderr)
            err_count += 1
            return False
        bars = bars_res.value
        print(f"  {sym}: {len(bars)} bars persisted", file=sys.stderr)
        ok_count += 1
        if args.include_dividends and not sym.startswith("^"):
            for year in range(start.year, end.year + 1):
                div_res = provider.dividends(instrument, year)
                if isinstance(div_res, Ok):
                    print(
                        f"    dividends {year}: {len(div_res.value)} events",
                        file=sys.stderr,
                    )
        return True

    # Stocks first; then indices. Inter-symbol pause throttles
    # the request rate so Yahoo doesn't 429 on every call.
    all_to_fetch = list(universe.stocks)
    for idx in indices:
        idx_id = idx.get("id", "")
        if idx_id:
            idx_currency = Currency(idx.get("currency", "EUR"))
            all_to_fetch.append(_index_stock(idx_id, idx_currency))

    for i, instrument in enumerate(all_to_fetch):
        _fetch(instrument)
        if args.sleep_seconds > 0 and i < len(all_to_fetch) - 1:
            _time.sleep(args.sleep_seconds)

    print(
        f"recorder: done — {ok_count} ok, {err_count} errors, "
        f"{skipped} skipped",
        file=sys.stderr,
    )
    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
