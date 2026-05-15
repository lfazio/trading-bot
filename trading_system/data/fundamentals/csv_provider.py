"""``CSVFundamentalsProvider`` — CSV-backed ``MarketDataProvider``
implementation serving only ``fundamentals(instr)``.

The provider loads + validates + freezes a snapshot at construction.
Schema-drift, malformed numerics, and stale rows are aggregated into
a single ``CsvLoadError`` so the operator fixes every bad row in one
cycle. Every other ``MarketDataProvider`` method returns
``Err("data:not_supported:csv_only")`` so a mis-wired caller fails
fast — silent empties are forbidden (REQ_F_FND_001).

REQ refs: REQ_F_FND_001..005, REQ_NF_FND_001, REQ_SDD_FND_001,
REQ_SDD_FND_002.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from trading_system.data.fundamentals.config import FundamentalsConfig
from trading_system.data.types import Bar, Fundamentals, Timeframe
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import Instrument
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Dividend
from trading_system.result import Err, Ok, Result


# ---------------------------------------------------------------------------
# Required header column set (REQ_SDD_FND_001)
# ---------------------------------------------------------------------------
_REQUIRED_COLUMNS: tuple[str, ...] = (
    "instrument_id",
    "yield_",
    "payout_ratio",
    "free_cash_flow_amount",
    "free_cash_flow_currency",
    "debt_equity",
    "dividend_history_years",
    "as_of_date",
)
_NUMERIC_DECIMAL_COLUMNS: tuple[str, ...] = (
    "yield_",
    "payout_ratio",
    "free_cash_flow_amount",
    "debt_equity",
)


class CsvLoadError(Exception):
    """Aggregated CSV-load failure — carries every per-row reason so
    the operator sees them all in one error message."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons: tuple[str, ...] = reasons


@dataclass(slots=True)
class CSVFundamentalsProvider:
    """Read-only snapshot of fundamentals loaded from a CSV.

    Construct via ``CSVFundamentalsProvider(config)``; the CSV is read,
    validated, and frozen immediately. The internal mapping is private —
    callers go through ``fundamentals(instr)`` (REQ_NF_FND_001). The only
    documented mutation hook is ``refresh()``.
    """

    config: FundamentalsConfig
    _today: Callable[[], date] = field(default=date.today)
    _snapshot: dict[InstrumentId, Fundamentals] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._snapshot = _load_csv(
            self.config.csv_path,
            max_age_days=self.config.max_age_days,
            today=self._today(),
        )

    # ------------------------------------------------------------------
    # MarketDataProvider Protocol surface
    # ------------------------------------------------------------------

    def fundamentals(self, instrument: Instrument) -> Result[Fundamentals, str]:
        f = self._snapshot.get(instrument.id)
        if f is None:
            return Err(f"data:not_found:fundamentals:{instrument.id}")
        return Ok(f)

    def bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Result[list[Bar], str]:
        return Err("data:not_supported:csv_only")

    def latest(self, instrument: Instrument) -> Result[Bar, str]:
        return Err("data:not_supported:csv_only")

    def dividends(
        self, instrument: Instrument, year: int
    ) -> Result[list[Dividend], str]:
        return Err("data:not_supported:csv_only")

    # ------------------------------------------------------------------
    # Operator tooling
    # ------------------------------------------------------------------

    def refresh(self) -> Result[None, str]:
        """Re-read the CSV — the only mutation hook (REQ_NF_FND_001).
        Intended for operator tooling / tests; the runtime should
        instantiate the provider once at startup."""
        try:
            self._snapshot = _load_csv(
                self.config.csv_path,
                max_age_days=self.config.max_age_days,
                today=self._today(),
            )
        except CsvLoadError as e:
            return Err(str(e))
        return Ok(None)


# ---------------------------------------------------------------------------
# Loader — pure function, aggregates errors
# ---------------------------------------------------------------------------


def _load_csv(
    path: Path,
    *,
    max_age_days: int,
    today: date,
) -> dict[InstrumentId, Fundamentals]:
    """Load + validate the CSV. Raises ``CsvLoadError`` aggregating every
    per-row reason. On success, returns a fresh mapping ready to be
    frozen onto the provider's snapshot."""
    if not path.exists():
        raise CsvLoadError((f"data:csv_not_found:{path}",))

    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        for column in _REQUIRED_COLUMNS:
            if column not in header:
                raise CsvLoadError((f"data:csv_schema:{column}",))

        snapshot: dict[InstrumentId, Fundamentals] = {}
        reasons: list[str] = []
        for raw_row in reader:
            row_or_err = _parse_row(raw_row, max_age_days=max_age_days, today=today)
            match row_or_err:
                case Err(reason):
                    reasons.append(reason)
                case Ok((instrument_id, fundamentals)):
                    if instrument_id in snapshot:
                        reasons.append(f"data:csv_duplicate:{instrument_id}")
                    else:
                        snapshot[instrument_id] = fundamentals

        if reasons:
            raise CsvLoadError(tuple(reasons))
        return snapshot


def _parse_row(
    row: dict[str, str],
    *,
    max_age_days: int,
    today: date,
) -> Result[tuple[InstrumentId, Fundamentals], str]:
    """Parse one CSV row → ``(InstrumentId, Fundamentals)`` or a
    categorised ``Err`` string."""
    instrument_id_raw = (row.get("instrument_id") or "").strip()
    if not instrument_id_raw:
        return Err("data:csv_malformed::instrument_id")
    instrument_id = InstrumentId(instrument_id_raw)

    # Stale-row gate runs BEFORE numeric parsing so a stale row whose
    # numerics also happen to be malformed still surfaces as `stale`
    # (the row is dropped either way — the staleness message is the
    # actionable one for the operator).
    as_of_raw = (row.get("as_of_date") or "").strip()
    if not as_of_raw:
        return Err(f"data:csv_malformed:{instrument_id}:as_of_date")
    try:
        as_of = date.fromisoformat(as_of_raw)
    except ValueError:
        return Err(f"data:csv_malformed:{instrument_id}:as_of_date")
    age_days = (today - as_of).days
    if age_days > max_age_days:
        return Err(f"data:stale:{instrument_id}:{as_of_raw}")

    # Numeric columns — Decimal via str(strip) so float noise never
    # leaks into the boundary (REQ_F_FND_002).
    parsed_numbers: dict[str, Decimal] = {}
    for column in _NUMERIC_DECIMAL_COLUMNS:
        raw = (row.get(column) or "").strip()
        if not raw:
            return Err(f"data:csv_malformed:{instrument_id}:{column}")
        try:
            parsed_numbers[column] = Decimal(raw)
        except InvalidOperation:
            return Err(f"data:csv_malformed:{instrument_id}:{column}")

    history_raw = (row.get("dividend_history_years") or "").strip()
    if not history_raw:
        return Err(f"data:csv_malformed:{instrument_id}:dividend_history_years")
    try:
        dividend_history_years = int(history_raw)
    except ValueError:
        return Err(f"data:csv_malformed:{instrument_id}:dividend_history_years")

    currency_raw = (row.get("free_cash_flow_currency") or "").strip()
    if not currency_raw:
        return Err(f"data:csv_malformed:{instrument_id}:free_cash_flow_currency")
    try:
        currency = Currency(currency_raw)
    except ValueError:
        return Err(f"data:csv_malformed:{instrument_id}:free_cash_flow_currency")

    try:
        fundamentals = Fundamentals(
            yield_=parsed_numbers["yield_"],
            payout_ratio=parsed_numbers["payout_ratio"],
            free_cash_flow=Money(
                parsed_numbers["free_cash_flow_amount"], currency
            ),
            debt_equity=parsed_numbers["debt_equity"],
            dividend_history_years=dividend_history_years,
        )
    except ValueError as e:
        # ``Fundamentals.__post_init__`` rejects negative values; surface
        # the offending field with a generic category so the operator
        # can correlate against the CSV row.
        return Err(f"data:csv_malformed:{instrument_id}:invariant:{e}")

    return Ok((instrument_id, fundamentals))
