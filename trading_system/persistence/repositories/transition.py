"""``TransitionRepository`` — durable regime-transition log.

Persisted alongside CR-008's existing repositories so a process restart
can rehydrate the ``TransitionTracker`` cursor with the latest persisted
regime (REQ_SDD_RGM_005). Schema lives in
``persistence/migrations/0002_regime.sql``; rows are written once per
confirmed ``TransitionEvent`` and never updated — append-only audit trail.

REQ refs:
- REQ_F_PER_002 — repository per aggregate root.
- REQ_F_PER_003 — explicit transactions; no partial writes.
- REQ_F_PER_009 — every read/write carries ``account_id``.
- REQ_NF_RGM_001 — round-trip equality preserved across the boundary.
- REQ_SDD_RGM_005 — schema + ``from_persistence`` rehydration hook.
- REQ_SDS_PER_002 — closed ``Err`` category set at the boundary.
- REQ_SDD_PER_002 — ``BEGIN IMMEDIATE`` wraps every write.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId, SnapshotId
from trading_system.persistence.connection import Connection
from trading_system.persistence.mappers import (
    row_to_transition_event,
    transition_event_to_row,
)
from trading_system.models.phase import TransitionEvent
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@dataclass(slots=True)
class TransitionRepository:
    """Durable backing for the regime ``TransitionTracker``."""

    conn: Connection

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def append(
        self,
        event: TransitionEvent,
        *,
        snapshot_id: SnapshotId,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Insert one confirmed ``TransitionEvent``. Duplicate
        ``(account_id, at)`` SHALL surface as
        ``Err("persistence:integrity:transitions:...")``."""
        if not snapshot_id:
            return Err("persistence:integrity:transitions:empty_snapshot_id")
        row = transition_event_to_row(event, str(account_id), str(snapshot_id))
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                """
                INSERT INTO transitions (
                    account_id, at, from_regime, to_regime,
                    confirmation_periods, snapshot_id
                ) VALUES (
                    :account_id, :at, :from_regime, :to_regime,
                    :confirmation_periods, :snapshot_id
                )
                """,
                row,
            )
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self._safe_rollback()
            return Err(f"persistence:integrity:transitions:{e}")
        except sqlite3.OperationalError as e:
            self._safe_rollback()
            return Err(f"persistence:locked:transitions:{e}")
        except sqlite3.Error as e:
            self._safe_rollback()
            return Err(f"persistence:corrupt:transitions:{e}")
        return Ok(None)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def latest(
        self,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[Option[TransitionEvent], str]:
        """Return the most recent persisted ``TransitionEvent`` for
        ``account_id`` — used by ``TransitionTracker.from_persistence``
        to seed the cursor on restart (REQ_SDD_RGM_005)."""
        try:
            cursor = self.conn.execute(
                "SELECT * FROM transitions WHERE account_id = ? "
                "ORDER BY at DESC LIMIT 1",
                (str(account_id),),
            )
            row = cursor.fetchone()
        except sqlite3.Error as e:
            return Err(f"persistence:corrupt:transitions:read:{e}")
        if row is None:
            return Ok(Nothing())
        try:
            event = row_to_transition_event(dict(row))
        except (ValueError, KeyError) as e:
            return Err(f"persistence:corrupt:transitions:parse:{e}")
        return Ok(Some(event))

    def history(
        self,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[tuple[TransitionEvent, ...], str]:
        """Return every persisted ``TransitionEvent`` for
        ``account_id`` in chronological order (``at`` ASC). Used by
        operator tooling and the dashboard's regime-history view."""
        try:
            cursor = self.conn.execute(
                "SELECT * FROM transitions WHERE account_id = ? "
                "ORDER BY at ASC",
                (str(account_id),),
            )
            rows = cursor.fetchall()
        except sqlite3.Error as e:
            return Err(f"persistence:corrupt:transitions:read:{e}")
        try:
            events = tuple(row_to_transition_event(dict(r)) for r in rows)
        except (ValueError, KeyError) as e:
            return Err(f"persistence:corrupt:transitions:parse:{e}")
        return Ok(events)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _safe_rollback(self) -> None:
        try:
            self.conn.rollback()
        except sqlite3.Error:
            pass
