"""``InstrumentBarRepository`` — CR-029 per-symbol bar persistence.

Backs the multi-instrument paper-trading runtime's tick-by-tick
bar fan-out: every universe symbol's polled bar lands in the
``instrument_bars`` table so the operator can later query "what
was BNP.PA's price when MC.PA was BOUGHT?".

The CR-021 yfinance cache produces byte-identical Decimals for
the same cached bar, so a duplicate-PK write on the same
``(account_id, instrument_id, bar_at)`` is **idempotent** — the
existing row's values match the incoming row's values + the
write is a no-op (REQ_F_PER_012 / REQ_SDD_PER_011).

REQ refs:
- REQ_F_PER_011 — repository surface + schema.
- REQ_F_PER_012 — runtime fan-out + idempotent duplicate-PK.
- REQ_F_PER_014 — byte-identical replay.
- REQ_SDD_PER_010..014 — design contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.data.types import Bar
from trading_system.models.identifiers import (
    DEFAULT_ACCOUNT_ID,
    AccountId,
    InstrumentId,
)
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
    OperationalError,
)
from trading_system.result import Err, Ok, Result


@dataclass(slots=True)
class InstrumentBarRepository:
    """SQLite-backed per-symbol bar persistence (CR-029)."""

    conn: Connection

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def append_bar(
        self,
        bar: Bar,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
        instrument_id: InstrumentId,
    ) -> Result[None, str]:
        """REQ_F_PER_011 / REQ_SDD_PER_011 — single-row write inside
        an explicit transaction. Duplicate PK on the same
        ``(account_id, instrument_id, bar_at)`` is idempotent (the
        cache produces byte-equal Decimals — REQ_F_PER_012)."""
        return self.append_bars(
            [(instrument_id, bar)], account_id=account_id
        )

    def append_bars(
        self,
        rows: Iterable[tuple[InstrumentId, Bar]],
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """REQ_F_PER_011 / REQ_SDD_PER_011 — batched multi-row write
        inside a SINGLE ``BEGIN IMMEDIATE`` / ``COMMIT`` transaction
        so a 40-symbol fan-out per tick is ONE COMMIT, not 40.

        Duplicate PK collisions are treated as idempotent: the
        ``INSERT OR IGNORE`` keeps the existing row in place.
        The CR-021 yfinance cache produces byte-identical Decimals
        for the same cached bar, so the existing row's values
        already match — the no-op is correct.
        """
        rows_list = list(rows)
        if not rows_list:
            return Ok(None)
        try:
            self.conn.begin_immediate()
            self.conn.executemany(
                "INSERT OR IGNORE INTO instrument_bars ("
                "  account_id, instrument_id, bar_at, "
                "  open, high, low, close, volume"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(account_id),
                        str(instrument_id),
                        bar.at.isoformat(),
                        str(bar.open),
                        str(bar.high),
                        str(bar.low),
                        str(bar.close),
                        str(bar.volume),
                    )
                    for instrument_id, bar in rows_list
                ],
            )
            self.conn.commit()
        except IntegrityError as e:
            self._safe_rollback()
            return Err(f"persistence:integrity:instrument_bars:{e}")
        except OperationalError as e:
            self._safe_rollback()
            return Err(f"persistence:locked:instrument_bars:{e}")
        except DatabaseError as e:
            self._safe_rollback()
            return Err(f"persistence:corrupt:instrument_bars:{e}")
        return Ok(None)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def bars_for(
        self,
        *,
        account_id: AccountId,
        instrument_id: InstrumentId,
        start: datetime,
        end: datetime,
    ) -> Result[tuple[Bar, ...], str]:
        """REQ_F_PER_011 / REQ_SDD_PER_011 — per-symbol range
        query ordered by ``bar_at ASC``. ``start`` and ``end`` are
        inclusive."""
        try:
            cursor = self.conn.execute(
                "SELECT bar_at, open, high, low, close, volume "
                "FROM instrument_bars "
                "WHERE account_id = ? AND instrument_id = ? "
                "AND bar_at >= ? AND bar_at <= ? "
                "ORDER BY bar_at ASC",
                (
                    str(account_id),
                    str(instrument_id),
                    start.isoformat(),
                    end.isoformat(),
                ),
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:instrument_bars:read:{e}")
        return Ok(tuple(_row_to_bar(dict(r)) for r in rows))

    def bars_at(
        self,
        *,
        account_id: AccountId,
        at: datetime,
    ) -> Result[Mapping[InstrumentId, Bar], str]:
        """REQ_F_PER_011 / REQ_SDD_PER_011 — cross-symbol slice for
        a single timestamp. Returns the universe's bar snapshot at
        ``at`` (operator's "what was the universe doing at time T"
        query). Empty mapping when no rows match — Ok({}), not Err."""
        try:
            cursor = self.conn.execute(
                "SELECT instrument_id, bar_at, open, high, low, close, volume "
                "FROM instrument_bars "
                "WHERE account_id = ? AND bar_at = ? "
                "ORDER BY instrument_id ASC",
                (str(account_id), at.isoformat()),
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:instrument_bars:read:{e}")
        out: dict[InstrumentId, Bar] = {}
        for r in rows:
            d = dict(r)
            out[InstrumentId(d["instrument_id"])] = _row_to_bar(d)
        return Ok(out)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _safe_rollback(self) -> None:
        try:
            self.conn.rollback()
        except DatabaseError:
            pass


def _row_to_bar(row: dict) -> Bar:
    return Bar(
        at=datetime.fromisoformat(row["bar_at"]),
        open=Decimal(row["open"]),
        high=Decimal(row["high"]),
        low=Decimal(row["low"]),
        close=Decimal(row["close"]),
        volume=Decimal(row["volume"]),
    )
