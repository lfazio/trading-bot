"""On-disk cache for the Yahoo Finance backtest adapter.

Filesystem-backed JSON Lines store keyed on
``(symbol, timeframe, start, end)`` for bars and ``(symbol, year)``
for dividends. The cache is the system of record for replay
determinism (REQ_NF_DAT_001 / REQ_SDS_DAT_002): once a tuple is
cached, subsequent backtests SHALL produce bit-identical results
regardless of upstream Yahoo revisions.

v1 backend: filesystem (JSON Lines). When CR-008 (persistence /
SQLite) lands as ``In-Progress``, the cache migrates to SQLite
behind the same ``YFinanceCache`` surface. The on-disk format here
keeps Decimal as TEXT and datetimes as ISO-8601 so the migration
preserves precision exactly.

REQ refs:
- REQ_F_DAT_004 — every fetch persisted to local cache before being returned.
- REQ_F_DAT_005 — cache hit reads pure-disk; no network.
- REQ_NF_DAT_001 — cache is the system of record.
- REQ_SDD_DAT_010 — CacheKey shape and equality.
- REQ_SDD_DAT_012 — error categories (cache_corrupt).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from trading_system.data.types import Bar
from trading_system.models.identifiers import InstrumentId
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Dividend
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Bar-cache key (REQ_SDD_DAT_010).

    ``timeframe`` is the ``Timeframe.value`` string (``"1d"``, ``"1h"``,
    ...). Equality requires every field to match at full timestamp
    precision; rendered to a stable filename so the on-disk layout is
    deterministic.
    """

    symbol: str
    timeframe: str
    start: datetime
    end: datetime

    def filename(self) -> str:
        # Slashes / colons are removed so the path is portable; the
        # full ISO timestamps still uniquely identify the range.
        s = self.start.isoformat().replace(":", "").replace("+", "Z")
        e = self.end.isoformat().replace(":", "").replace("+", "Z")
        return f"{s}__{e}_bars.jsonl"


@dataclass(slots=True)
class YFinanceCache:
    """Filesystem-backed JSON Lines cache.

    Layout:
        <root>/<symbol>/<timeframe>/<start>__<end>_bars.jsonl
        <root>/<symbol>/dividends/<year>.jsonl
    """

    root: Path

    # ------------------------------------------------------------------
    # Bars
    # ------------------------------------------------------------------

    def get_bars(self, key: CacheKey) -> Option[list[Bar]]:
        path = self._bars_path(key)
        if not path.exists():
            return Nothing()
        match self._read_jsonl_bars(path):
            case Ok(bars):
                return Some(bars)
            case Err(_):
                # Corrupted file is treated as a miss; the put_bars
                # path will overwrite if/when network is allowed.
                # The caller decides whether to surface this.
                return Nothing()

    def put_bars(self, key: CacheKey, bars: list[Bar]) -> Result[None, str]:
        path = self._bars_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [_bar_to_record(b) for b in bars]
        return _write_jsonl(path, records)

    # ------------------------------------------------------------------
    # Dividends
    # ------------------------------------------------------------------

    def get_dividends(self, symbol: str, year: int, currency: Currency) -> Option[list[Dividend]]:
        path = self._dividends_path(symbol, year)
        if not path.exists():
            return Nothing()
        match self._read_jsonl_dividends(path, currency):
            case Ok(divs):
                return Some(divs)
            case Err(_):
                return Nothing()

    def put_dividends(self, symbol: str, year: int, dividends: list[Dividend]) -> Result[None, str]:
        path = self._dividends_path(symbol, year)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [_dividend_to_record(d) for d in dividends]
        return _write_jsonl(path, records)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def has_bars(self, key: CacheKey) -> bool:
        return self._bars_path(key).exists()

    def has_dividends(self, symbol: str, year: int) -> bool:
        return self._dividends_path(symbol, year).exists()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _bars_path(self, key: CacheKey) -> Path:
        safe_symbol = key.symbol.replace("/", "_")
        safe_tf = key.timeframe.replace("/", "_")
        return self.root / safe_symbol / safe_tf / key.filename()

    def _dividends_path(self, symbol: str, year: int) -> Path:
        safe_symbol = symbol.replace("/", "_")
        return self.root / safe_symbol / "dividends" / f"{year}.jsonl"

    def _read_jsonl_bars(self, path: Path) -> Result[list[Bar], str]:
        try:
            text = path.read_text()
        except OSError as e:
            return Err(f"data:cache_corrupt:{path}:read:{e}")
        out: list[Bar] = []
        for line_no, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                out.append(_record_to_bar(rec))
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                return Err(f"data:cache_corrupt:{path}:line {line_no}:{e}")
        return Ok(out)

    def _read_jsonl_dividends(self, path: Path, currency: Currency) -> Result[list[Dividend], str]:
        try:
            text = path.read_text()
        except OSError as e:
            return Err(f"data:cache_corrupt:{path}:read:{e}")
        out: list[Dividend] = []
        for line_no, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                out.append(_record_to_dividend(rec, currency))
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                return Err(f"data:cache_corrupt:{path}:line {line_no}:{e}")
        return Ok(out)


# ----------------------------------------------------------------------
# Record converters — JSON-friendly (Decimal as str, datetime as ISO)
# ----------------------------------------------------------------------


def _bar_to_record(b: Bar) -> dict:
    return {
        "at": b.at.isoformat(),
        "open": str(b.open),
        "high": str(b.high),
        "low": str(b.low),
        "close": str(b.close),
        "volume": str(b.volume),
    }


def _record_to_bar(rec: dict) -> Bar:
    return Bar(
        at=datetime.fromisoformat(rec["at"]),
        open=Decimal(rec["open"]),
        high=Decimal(rec["high"]),
        low=Decimal(rec["low"]),
        close=Decimal(rec["close"]),
        volume=Decimal(rec["volume"]),
    )


def _dividend_to_record(d: Dividend) -> dict:
    return {
        "instrument": str(d.instrument),
        "ex_date": d.ex_date.isoformat(),
        "pay_date": d.pay_date.isoformat(),
        "amount_gross": str(d.amount_gross.amount),
        "currency": d.amount_gross.currency.value,
    }


def _record_to_dividend(rec: dict, currency: Currency) -> Dividend:
    if rec["currency"] != currency.value:
        raise ValueError(f"cache currency {rec['currency']!r} != requested {currency.value!r}")
    return Dividend(
        instrument=InstrumentId(rec["instrument"]),
        ex_date=datetime.fromisoformat(rec["ex_date"]),
        pay_date=datetime.fromisoformat(rec["pay_date"]),
        amount_gross=Money(Decimal(rec["amount_gross"]), currency),
    )


def _write_jsonl(path: Path, records: list[dict]) -> Result[None, str]:
    try:
        with path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, separators=(",", ":")))
                fh.write("\n")
        return Ok(None)
    except OSError as e:
        return Err(f"data:cache_corrupt:{path}:write:{e}")
