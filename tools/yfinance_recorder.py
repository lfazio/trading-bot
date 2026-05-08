#!/usr/bin/env python3
"""Operator-run recorder: populate the ``YFinanceCache`` from Yahoo Finance.

This is the **only** path in the codebase that imports ``yfinance`` /
``pandas`` for real. The runtime, the test suite, and CI all stay
hermetic — they read from the cache the recorder produced.

Install the optional extra first::

    pip install trading-bot[yfinance]

Usage::

    python tools/yfinance_recorder.py \
        --symbol ASML.AS \
        --exchange AS \
        --currency EUR \
        --timeframe 1d \
        --start 2020-01-01 \
        --end   2026-05-08 \
        --cache-root .cache/yfinance \
        --include-dividends

Subsequent backtests read the cache with
``YFinanceMarketDataProvider(allow_network=False)`` and produce
bit-identical results regardless of upstream Yahoo revisions
(REQ_NF_DAT_001).

REQ refs: REQ_F_DAT_002, REQ_F_DAT_004, REQ_F_DAT_006,
REQ_NF_DAT_001, REQ_SDS_DAT_002.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from trading_system.data.types import Timeframe
from trading_system.data.yfinance import YFinanceCache, YFinanceMarketDataProvider
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err, Ok


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate the YFinanceCache from Yahoo Finance (operator-run).",
    )
    parser.add_argument("--symbol", required=True, help="Yahoo ticker, e.g. ASML.AS")
    parser.add_argument(
        "--exchange",
        required=True,
        help="Instrument.exchange value (e.g. AS, PA, DE, L); drives Yahoo-suffix lookup.",
    )
    parser.add_argument(
        "--currency",
        required=True,
        help="ISO-4217 code for Money denomination (EUR / USD / GBP / CHF).",
    )
    parser.add_argument(
        "--timeframe",
        default="1d",
        choices=[tf.value for tf in Timeframe],
        help="Bar resolution (default: 1d).",
    )
    parser.add_argument("--start", required=True, help="ISO-8601 start (e.g. 2020-01-01).")
    parser.add_argument("--end", required=True, help="ISO-8601 end (e.g. 2026-05-08).")
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(".cache/yfinance"),
        help="Cache root directory (default: .cache/yfinance).",
    )
    parser.add_argument(
        "--include-dividends",
        action="store_true",
        help="Also fetch the dividend history per calendar year in [start..end].",
    )
    parser.add_argument(
        "--isin",
        default="",
        help="Optional ISIN (purely metadata; not required for the fetch).",
    )
    parser.add_argument(
        "--sector",
        default="",
        help="Optional sector tag (metadata only).",
    )
    parser.add_argument(
        "--country",
        default="",
        help="Optional country tag (metadata only).",
    )
    return parser.parse_args()


def _parse_date(s: str) -> datetime:
    """Accept ISO-8601 date or datetime; pin to UTC."""
    dt = datetime.fromisoformat(s) if "T" in s else datetime.fromisoformat(s + "T00:00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def main() -> int:
    args = _parse_args()
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if start >= end:
        print(f"start ({start}) must be < end ({end})", file=sys.stderr)
        return 2

    currency = Currency(args.currency)
    timeframe = Timeframe(args.timeframe)
    instrument = Stock(
        id=InstrumentId(args.symbol),
        symbol=args.symbol.split(".", 1)[0],  # strip Yahoo suffix for our domain id
        exchange=args.exchange,
        currency=currency,
        cls=InstrumentClass.STOCK,
        isin=args.isin or "RECORDER_NO_ISIN",
        sector=args.sector or "RECORDER",
        country=args.country or "RECORDER",
    )

    cache = YFinanceCache(root=args.cache_root)
    # allow_network=True — this script's whole purpose is to populate
    # the cache from Yahoo. Backtests / CI use allow_network=False
    # against the cache the script produced.
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=currency,
        allow_network=True,
    )

    print(
        f"recorder: fetching bars {args.symbol} ({timeframe.value}) "
        f"{start.date().isoformat()}..{end.date().isoformat()} "
        f"-> {args.cache_root}",
        file=sys.stderr,
    )
    bars_res = provider.bars(instrument, timeframe, start, end)
    match bars_res:
        case Ok(bars):
            print(f"  bars: {len(bars)} rows persisted", file=sys.stderr)
        case Err(reason):
            print(f"  bars: ERROR {reason}", file=sys.stderr)
            return 1

    if args.include_dividends:
        for year in range(start.year, end.year + 1):
            print(f"recorder: fetching dividends {args.symbol} {year}", file=sys.stderr)
            div_res = provider.dividends(instrument, year)
            match div_res:
                case Ok(divs):
                    print(f"  dividends {year}: {len(divs)} events", file=sys.stderr)
                case Err(reason):
                    print(f"  dividends {year}: ERROR {reason}", file=sys.stderr)
                    # Don't fail the whole run on a missing year.

    print("recorder: done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
