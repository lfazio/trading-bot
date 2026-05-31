"""``PaperSessionRepository`` — CR-019 follow-up §6 paper-session
metadata persistence.

Backs the recovery wizard's "one-click resume" workflow: every
``PaperTradingRuntime`` construction writes a row carrying the
wizard's inputs (universe / strategy / instrument symbol /
starting capital / bar source); ``RuntimeRegistry.resume_from_
persistence`` reads them back so a webapp restart can rehydrate
the runtime without re-asking the operator.

Write-once-append semantics: re-writing the same ``account_id``
is idempotent if the metadata matches (the CR-021 cache contract
owns the bytes; mismatched metadata indicates a session-identity
bug + should NOT silently succeed — surfaces as
``persistence:integrity:paper_sessions:<account_id>``).

REQ refs:
- REQ_F_PAP_003 — session persistence so webapp restart resumes
  cleanly without operator action.
- REQ_SDD_WEB2_005 — `resume_from_persistence` enrichment.
- REQ_F_PER_002 / REQ_F_PER_003 / REQ_F_PER_005 / REQ_F_PER_009 —
  one repository per aggregate; Decimal-as-TEXT;
  per-account scoping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.money import Currency, Money
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
    OperationalError,
)
from trading_system.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class PaperSessionRow:
    """One row of the ``paper_sessions`` table — the rehydration
    metadata for a paper-trading session."""

    account_id: AccountId
    universe: str
    strategy_id: StrategyId
    instrument_symbol: str
    starting_capital: Money
    bar_source: str
    started_at: datetime
    mode_tag: str = "paper"

    def __post_init__(self) -> None:
        if not str(self.account_id).strip():
            raise ValueError(
                "PaperSessionRow.account_id must be non-empty"
            )
        if not self.universe.strip():
            raise ValueError(
                "PaperSessionRow.universe must be non-empty"
            )
        if not str(self.strategy_id).strip():
            raise ValueError(
                "PaperSessionRow.strategy_id must be non-empty"
            )
        if not self.instrument_symbol.strip():
            raise ValueError(
                "PaperSessionRow.instrument_symbol must be non-empty"
            )
        if self.starting_capital.amount <= 0:
            raise ValueError(
                "PaperSessionRow.starting_capital must be > 0, "
                f"got {self.starting_capital.amount}"
            )
        if self.bar_source not in ("simulated", "yfinance"):
            raise ValueError(
                f"PaperSessionRow.bar_source must be one of "
                f"('simulated', 'yfinance'), got {self.bar_source!r}"
            )


@dataclass(slots=True)
class PaperSessionRepository:
    """SQLite-backed paper-session metadata persistence."""

    conn: Connection

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def append_session(self, row: PaperSessionRow) -> Result[None, str]:
        """Insert a new ``paper_sessions`` row. Duplicate
        ``account_id`` re-writes (operator re-launches the wizard
        for the same id) surface as
        ``Err("persistence:integrity:paper_sessions:duplicate")`` —
        the runtime detects this + offers a "stop existing first"
        flow rather than silently shadowing the prior session."""
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                "INSERT INTO paper_sessions ("
                "  account_id, universe, strategy_id, instrument_symbol, "
                "  starting_capital, currency, bar_source, started_at, "
                "  mode_tag"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(row.account_id),
                    row.universe,
                    str(row.strategy_id),
                    row.instrument_symbol,
                    str(row.starting_capital.amount),
                    row.starting_capital.currency.value,
                    row.bar_source,
                    row.started_at.isoformat(),
                    row.mode_tag,
                ),
            )
            self.conn.commit()
        except IntegrityError:
            self._safe_rollback()
            return Err(
                f"persistence:integrity:paper_sessions:duplicate:{row.account_id}"
            )
        except OperationalError as e:
            self._safe_rollback()
            return Err(f"persistence:locked:paper_sessions:{e}")
        except DatabaseError as e:
            self._safe_rollback()
            return Err(f"persistence:corrupt:paper_sessions:{e}")
        return Ok(None)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(
        self, account_id: AccountId
    ) -> Result[PaperSessionRow | None, str]:
        """Return the row for ``account_id`` or ``None`` when the
        session metadata wasn't persisted (e.g., pre-§6 sessions)."""
        try:
            cursor = self.conn.execute(
                "SELECT account_id, universe, strategy_id, instrument_symbol, "
                "  starting_capital, currency, bar_source, started_at, mode_tag "
                "FROM paper_sessions WHERE account_id = ?",
                (str(account_id),),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:paper_sessions:read:{e}")
        if row is None:
            return Ok(None)
        return Ok(_row_to_paper_session(dict(row)))

    def list_all(self) -> Result[tuple[PaperSessionRow, ...], str]:
        """Return every persisted paper-session row sorted by
        ``started_at DESC`` so the recovery wizard shows the most
        recent session first."""
        try:
            cursor = self.conn.execute(
                "SELECT account_id, universe, strategy_id, instrument_symbol, "
                "  starting_capital, currency, bar_source, started_at, mode_tag "
                "FROM paper_sessions "
                "ORDER BY started_at DESC, account_id ASC"
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:paper_sessions:read:{e}")
        return Ok(tuple(_row_to_paper_session(dict(r)) for r in rows))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _safe_rollback(self) -> None:
        try:
            self.conn.rollback()
        except DatabaseError:
            pass


def _row_to_paper_session(row: dict) -> PaperSessionRow:
    return PaperSessionRow(
        account_id=AccountId(row["account_id"]),
        universe=row["universe"],
        strategy_id=StrategyId(row["strategy_id"]),
        instrument_symbol=row["instrument_symbol"],
        starting_capital=Money(
            Decimal(row["starting_capital"]),
            Currency(row["currency"]),
        ),
        bar_source=row["bar_source"],
        started_at=datetime.fromisoformat(row["started_at"]),
        mode_tag=row["mode_tag"],
    )
