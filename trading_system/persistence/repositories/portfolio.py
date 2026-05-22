"""``PortfolioRepository`` — durable equity-curve store.

Public surface: one method per state transition the engine
actually performs (``append_equity_point``) and one per query the
engine actually issues (``equity_curve``). No speculative methods
— callers that need a different shape add a new method
deliberately (REQ_SDS_PER_002).

REQ refs:
- REQ_F_PER_002 — repository per aggregate root.
- REQ_F_PER_003 — explicit transactions; no partial writes.
- REQ_F_PER_005 — Decimal as TEXT, datetime as ISO-8601 at the
  boundary (delegated to ``mappers``).
- REQ_F_PER_009 — every read/write carries ``account_id``; default
  is the sentinel ``DEFAULT_ACCOUNT_ID``.
- REQ_NF_PER_001 — round-trip equality preserved.
- REQ_SDS_PER_002 — closed ``Err`` category set at the boundary.
- REQ_SDD_PER_002 — ``BEGIN IMMEDIATE`` wraps every write.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId
from trading_system.persistence.connection import Connection
from trading_system.persistence.mappers import (
    equity_point_to_row,
    row_to_equity_point,
)
from trading_system.result import Err, Ok, Result


@dataclass(slots=True)
class PortfolioRepository:
    """Durable backing for ``Portfolio.equity_curve``."""

    conn: Connection

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def append_equity_point(
        self,
        point: EquityPoint,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Insert one equity-curve point. Duplicate
        ``(account_id, at)`` SHALL surface as
        ``Err("persistence:integrity:...")``."""
        row = equity_point_to_row(point, str(account_id))
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                """
                INSERT INTO equity_points (
                    account_id, at,
                    equity_gross_amount, equity_gross_currency,
                    equity_after_tax_amount, equity_after_tax_currency,
                    drawdown_pct
                ) VALUES (
                    :account_id, :at,
                    :equity_gross_amount, :equity_gross_currency,
                    :equity_after_tax_amount, :equity_after_tax_currency,
                    :drawdown_pct
                )
                """,
                row,
            )
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self._safe_rollback()
            return Err(f"persistence:integrity:equity_points:{e}")
        except sqlite3.OperationalError as e:
            self._safe_rollback()
            return Err(f"persistence:locked:equity_points:{e}")
        except sqlite3.Error as e:
            self._safe_rollback()
            return Err(f"persistence:corrupt:equity_points:{e}")
        return Ok(None)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def equity_curve(
        self,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[tuple[EquityPoint, ...], str]:
        """Return every recorded equity-curve point for
        ``account_id``, ordered ascending by ``at``."""
        try:
            cursor = self.conn.execute(
                "SELECT * FROM equity_points WHERE account_id = ? ORDER BY at ASC",
                (str(account_id),),
            )
        except sqlite3.Error as e:
            return Err(f"persistence:corrupt:equity_points:read:{e}")
        try:
            points = tuple(row_to_equity_point(dict(row)) for row in cursor.fetchall())
        except (ValueError, KeyError) as e:
            return Err(f"persistence:corrupt:equity_points:parse:{e}")
        return Ok(points)

    def list_account_ids_with_prefix(
        self, prefix: str
    ) -> Result[tuple[AccountId, ...], str]:
        """Return every distinct ``account_id`` present in
        ``equity_points`` matching ``prefix``, ordered ascending.

        Consumed by CR-019 step 1 (b) (REQ_F_PAP_003) — the
        paper-trading runtime registry calls this with
        ``"paper-"`` to enumerate resumable sessions after a
        webapp restart.

        Returns an empty tuple when no rows match; the call is
        cheap (single indexed query) but issued once per restart.
        """
        if not prefix:
            return Err("persistence:bad_prefix:empty")
        try:
            cursor = self.conn.execute(
                "SELECT DISTINCT account_id FROM equity_points "
                "WHERE account_id LIKE ? "
                "ORDER BY account_id ASC",
                (prefix + "%",),
            )
        except sqlite3.Error as e:
            return Err(f"persistence:corrupt:equity_points:list:{e}")
        try:
            ids = tuple(AccountId(row["account_id"]) for row in cursor.fetchall())
        except (ValueError, KeyError) as e:
            return Err(f"persistence:corrupt:equity_points:list_parse:{e}")
        return Ok(ids)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _safe_rollback(self) -> None:
        try:
            self.conn.rollback()
        except sqlite3.Error:
            # The connection may already be in an aborted state; we
            # never bubble a rollback failure on top of the original
            # error.
            pass
