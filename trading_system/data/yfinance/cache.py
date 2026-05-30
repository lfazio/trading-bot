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
from datetime import UTC, datetime
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
        # Exact-key fast path — bit-identical to the legacy lookup.
        path = self._bars_path(key)
        if path.exists():
            match self._read_jsonl_bars(path):
                case Ok(bars):
                    return Some(bars)
                case Err(_):
                    # Corrupted file → fall through to the envelope
                    # scan; another file may cover the same window.
                    pass
        # CR-021 range-aware second pass — any cached file whose
        # stored [file_start, file_end] envelopes the requested
        # [start, end] satisfies the query after slicing.
        return self._envelope_lookup(key)

    def _envelope_lookup(self, key: CacheKey) -> Option[list[Bar]]:
        """CR-021 — search every cached file under the symbol /
        timeframe for one whose stored range envelopes the
        requested ``[start, end]``. Returns the bars sliced to that
        window, byte-identical to what an exact-key recorder run
        would have produced.

        The widest enveloping file wins (largest ``file_end -
        file_start``); ties broken by lexicographic filename order.
        Older recorder runs are inspected; corrupted files are
        skipped.
        """
        safe_symbol = key.symbol.replace("/", "_")
        safe_tf = key.timeframe.replace("/", "_")
        sym_tf_dir = self.root / safe_symbol / safe_tf
        if not sym_tf_dir.exists():
            return Nothing()
        # Filenames carry tz-aware datetimes (``_parse_filename_window``
        # promotes naïve values to UTC). The caller's ``key.start`` /
        # ``key.end`` MAY arrive naïve when the strategy computed
        # ``state.at - timedelta(...)`` against a naïve tick (e.g., live
        # yfinance polling on older Python paths). Normalise both sides
        # to UTC-aware before comparing so the predicate doesn't raise
        # ``TypeError: can't compare offset-naive and offset-aware
        # datetimes``. The cache stays the system of record per
        # REQ_NF_DAT_001 — promoting a naïve key to UTC is a no-op for
        # already-UTC inputs.
        key_start = (
            key.start.replace(tzinfo=UTC) if key.start.tzinfo is None else key.start
        )
        key_end = (
            key.end.replace(tzinfo=UTC) if key.end.tzinfo is None else key.end
        )
        candidates: list[tuple[int, str, Path]] = []
        for path in sym_tf_dir.glob("*_bars.jsonl"):
            window = _parse_filename_window(path.name)
            if window is None:
                continue
            file_start, file_end = window
            if file_start <= key_start and file_end >= key_end:
                width = int((file_end - file_start).total_seconds())
                # Negative width so ``sort`` returns widest first.
                candidates.append((-width, path.name, path))
        if not candidates:
            return Nothing()
        candidates.sort()
        for _, _, path in candidates:
            match self._read_jsonl_bars(path):
                case Ok(bars):
                    sliced = [
                        b for b in bars
                        if key_start <= _as_utc(b.at) <= key_end
                    ]
                    return Some(sliced)
                case Err(_):
                    continue
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


def _parse_filename_window(name: str) -> tuple[datetime, datetime] | None:
    """CR-021 — invert ``CacheKey.filename()``.

    Filename shape: ``<start_iso>__<end_iso>_bars.jsonl`` where the
    ISO timestamps have had ``:`` removed and ``+`` replaced with
    ``Z`` (see ``CacheKey.filename``). We reverse those substitutions
    + reattach ``:`` to ``HHMMSS`` so ``datetime.fromisoformat``
    succeeds. Returns ``None`` for names that don't match the schema
    (the envelope-lookup caller treats those as non-candidates).
    """
    if not name.endswith("_bars.jsonl"):
        return None
    stem = name[: -len("_bars.jsonl")]
    parts = stem.split("__")
    if len(parts) != 2:
        return None
    start_s, end_s = parts
    try:
        s_dt = _decode_iso(start_s)
        e_dt = _decode_iso(end_s)
    except ValueError:
        return None
    return (s_dt, e_dt)


def _decode_iso(token: str) -> datetime:
    """Reverse ``CacheKey.filename`` token encoding.

    Encoding strips ``:`` and rewrites the ``+`` of the offset to
    ``Z``; decoding undoes both. The date / time / offset segments
    are positional in ISO-8601: ``YYYY-MM-DDTHHMMSS[.ffffff][Z±HHMM]``.
    """
    # Locate the offset boundary (``Z`` immediately preceded by a
    # date/time character; the literal trailing ``Z0000`` form).
    body = token
    offset = ""
    z_idx = body.rfind("Z")
    if z_idx >= 0 and z_idx + 1 < len(body):
        offset_raw = body[z_idx + 1 :]
        # Expect 4 digits ``HHMM`` after the Z, optionally signed.
        if len(offset_raw) >= 4 and offset_raw.lstrip("+-")[:4].isdigit():
            sign = "+" if not offset_raw.startswith("-") else "-"
            digits = offset_raw.lstrip("+-")
            offset = f"{sign}{digits[:2]}:{digits[2:4]}"
            body = body[:z_idx]
    # Split into ``date`` and ``time`` (+ optional fractional secs).
    if "T" not in body:
        raise ValueError(f"missing T separator in {token!r}")
    date_part, time_part = body.split("T", 1)
    # ``HHMMSS`` or ``HHMMSS.ffffff`` — re-insert the colons.
    if "." in time_part:
        hms, frac = time_part.split(".", 1)
    else:
        hms, frac = time_part, ""
    if len(hms) != 6 or not hms.isdigit():
        raise ValueError(f"unexpected HMS shape in {token!r}: {hms!r}")
    iso_time = f"{hms[0:2]}:{hms[2:4]}:{hms[4:6]}"
    if frac:
        iso_time = f"{iso_time}.{frac}"
    iso = f"{date_part}T{iso_time}{offset}"
    parsed = datetime.fromisoformat(iso)
    # Cache files from older recorder runs encoded naïve datetimes
    # (no ``Z`` suffix); normalise to UTC on parse so the envelope
    # predicate compares uniformly tz-aware values.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _as_utc(dt: datetime) -> datetime:
    """Promote a naïve datetime to UTC-aware. No-op for tz-aware inputs.

    Defensive helper used by the envelope-lookup predicates so a
    legacy cache file holding naïve datetimes (older recorder runs)
    or a naïve caller key compare uniformly against tz-aware values.
    The cache stays the system of record per REQ_NF_DAT_001 — the
    bytes don't change, only the comparison normalises."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


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
    at = datetime.fromisoformat(rec["at"])
    # Cache files historically stored naïve datetimes (yfinance's
    # daily bars come from pandas Timestamps without tz). Normalise
    # to UTC on read so cross-file comparisons (e.g.
    # ``_scan_latest_cached_bar``) never mix offset-naive with
    # offset-aware values.
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return Bar(
        at=at,
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
