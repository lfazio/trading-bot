"""Generate bundled offline yfinance fixtures — MVP-1 of CR-016.

Writes deterministic synthetic OHLCV + dividend JSON Lines fixtures
under ``data/yfinance-fixtures/<symbol>/<timeframe>/...`` so the
trading-bot can be run **without network access**. The fixtures
are SYNTHETIC — they're a deterministic random walk, NOT real
historical Yahoo data. They unblock the network-failure mode that
the v0.2 User-Manual verification pass surfaced (the recorder's
`curl: (7)` error).

Operators wanting real historical data still run
``tools/yfinance_recorder.py --symbol ... --exchange ... --currency ...``
against the live Yahoo Finance API. The bundled fixtures are the
**always-available baseline**, not a replacement.

Usage::

    python tools/generate_bundled_fixtures.py

Writes to ``data/yfinance-fixtures/`` relative to the repo root.
Re-running produces byte-identical output (deterministic seed per
symbol) so the fixtures track the SHA in git.

REQ refs: REQ_F_RPT_001 (bundle the test environment), REQ_F_DAT_004
(cache is system of record), REQ_NF_DAT_001 (replay determinism).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path


# A small EU dividend-aristocrat starter set. Real tickers, fake bars.
# Operators wanting real data run the recorder against Yahoo.
@dataclass(frozen=True, slots=True)
class _Symbol:
    ticker: str
    currency: str
    starting_price: Decimal
    annual_drift: Decimal       # mean log-return per year
    annual_vol: Decimal         # vol per year
    annual_dividend: Decimal    # total annual dividend per share


_UNIVERSE: tuple[_Symbol, ...] = (
    _Symbol("ASML.AS", "EUR", Decimal("600"), Decimal("0.12"), Decimal("0.30"), Decimal("6.00")),
    _Symbol("BNP.PA", "EUR", Decimal("55"), Decimal("0.06"), Decimal("0.25"), Decimal("4.40")),
    _Symbol("SAN.PA", "EUR", Decimal("90"), Decimal("0.05"), Decimal("0.20"), Decimal("3.40")),
)


# Fixed bundled-fixture window. The MVP-v1 demo defaults to this
# range so the bundled fixtures cover the lookup exactly.
_START = datetime(2024, 1, 2, tzinfo=UTC)
_END = datetime(2024, 12, 31, tzinfo=UTC)


def _trading_days(start: datetime, end: datetime) -> list[datetime]:
    """Yield every weekday between ``start`` and ``end`` inclusive.

    Synthetic data uses weekday-only — the real Euronext calendar
    has holiday gaps but the deterministic random-walk doesn't
    pretend to model them. Operators wanting real-history bars
    use the live recorder.
    """
    out: list[datetime] = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # 0-4 = Mon..Fri
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed_for(symbol: str) -> int:
    """Deterministic seed per symbol so two runs of this script
    produce byte-identical fixtures."""
    h = hashlib.sha256(f"trading-bot-fixture:{symbol}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def _generate_bars(spec: _Symbol) -> list[dict]:
    """One-day random walk under (annual_drift, annual_vol) scaled
    to per-day. Returns JSON Lines records matching the
    YFinanceCache schema (Decimal-as-TEXT; ISO-8601 datetimes).
    """
    rng = random.Random(_seed_for(spec.ticker))
    days = _trading_days(_START, _END)
    n = len(days)
    # Per-day mean + std-dev (geometric Brownian motion).
    mu = float(spec.annual_drift) / 252
    sigma = float(spec.annual_vol) / (252 ** 0.5)
    price = float(spec.starting_price)
    records: list[dict] = []
    for day in days:
        # Daily log-return.
        r = rng.gauss(mu, sigma)
        # OHLC: open ~= prior close; high/low bracket the new close.
        open_p = price
        close_p = open_p * (2.71828 ** r)
        intraday_range = abs(rng.gauss(0.0, sigma * 0.5))
        high_p = max(open_p, close_p) * (1 + intraday_range / 2)
        low_p = min(open_p, close_p) * (1 - intraday_range / 2)
        # Volume — log-normal centred at 1M shares.
        volume = int(1_000_000 * (2.71828 ** rng.gauss(0.0, 0.5)))
        records.append({
            "at": day.isoformat(),
            "open": f"{open_p:.4f}",
            "high": f"{high_p:.4f}",
            "low": f"{low_p:.4f}",
            "close": f"{close_p:.4f}",
            "volume": str(volume),
            "currency": spec.currency,
        })
        price = close_p
    return records


def _generate_dividends(spec: _Symbol) -> list[dict]:
    """Four quarterly dividends per year, each = annual_dividend / 4
    paid on the first trading day of months 3 / 6 / 9 / 12."""
    per_q = spec.annual_dividend / Decimal("4")
    out: list[dict] = []
    for month in (3, 6, 9, 12):
        ex_date = datetime(2024, month, 1, tzinfo=UTC)
        # If month-start is a weekend, skip to next weekday.
        while ex_date.weekday() >= 5:
            ex_date += timedelta(days=1)
        out.append({
            "instrument": spec.ticker,
            "ex_date": ex_date.isoformat(),
            "pay_date": ex_date.isoformat(),
            "amount": str(per_q),
            "currency": spec.currency,
        })
    return out


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Sorted-key + no-spaces canonical JSON, one record per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True))
            fh.write("\n")


def _filename_for(start: datetime, end: datetime) -> str:
    """Mirror the YFinanceCache.CacheKey.filename() format."""
    s = start.isoformat().replace(":", "").replace("+", "Z")
    e = end.isoformat().replace(":", "").replace("+", "Z")
    return f"{s}__{e}_bars.jsonl"


def generate(root: Path) -> int:
    """Write every fixture under ``root``. Returns the file count."""
    written = 0
    for spec in _UNIVERSE:
        # Bars — one file per (symbol, timeframe, range).
        bars = _generate_bars(spec)
        bars_dir = root / spec.ticker / "1d"
        bars_path = bars_dir / _filename_for(_START, _END)
        _write_jsonl(bars_path, bars)
        written += 1
        # Dividends — one file per year (just 2024 in MVP-v1).
        divs = _generate_dividends(spec)
        divs_path = root / spec.ticker / "dividends" / "2024.jsonl"
        _write_jsonl(divs_path, divs)
        written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("data/yfinance-fixtures"),
        help="Output root (default: data/yfinance-fixtures relative to CWD).",
    )
    args = parser.parse_args()
    n = generate(args.root)
    print(f"generate_bundled_fixtures: wrote {n} files under {args.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
