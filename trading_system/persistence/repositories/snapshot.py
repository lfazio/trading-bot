"""``KillSwitchSnapshotRepository`` — the SQLite-backed default
``SnapshotSink`` implementation (CR-008 / REQ_F_PER_008 /
REQ_SDD_PER_007).

This module is a drop-in for ``safety.snapshot.FileSnapshotSink``: it
satisfies the same ``SnapshotSink`` Protocol (one ``record(snapshot)``
method) and additionally exposes ``get(snapshot_id)`` so an operator —
or the recovery flow — can replay the archived snapshot by id.

The migration toggle (``safety.snapshot_backend: filesystem |
persistence``) lives at the wiring layer; the legacy ``FileSnapshotSink``
remains available so existing operators can keep the JSON-lines export.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_system.models.identifiers import (
    DEFAULT_ACCOUNT_ID,
    AccountId,
    SnapshotId,
)
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
    OperationalError,
)
from trading_system.persistence.mappers import (
    audit_snapshot_to_row,
    row_to_audit_snapshot,
)
from trading_system.result import Err, Ok, Result
from trading_system.safety.snapshot import AuditSnapshot


@dataclass(slots=True)
class KillSwitchSnapshotRepository:
    """SQLite-backed ``SnapshotSink``. The ``record`` method is the
    Protocol-conforming write path; ``write`` is a ``Result``-typed
    alternative that surfaces persistence errors to callers that want
    to handle them explicitly."""

    conn: Connection
    account_id: AccountId = DEFAULT_ACCOUNT_ID

    def write(self, snapshot: AuditSnapshot) -> Result[None, str]:
        row = audit_snapshot_to_row(snapshot, str(self.account_id))
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                "INSERT INTO ks_snapshots "
                "(account_id, snapshot_id, captured_at, snapshot_json) "
                "VALUES (:account_id, :snapshot_id, :captured_at, :snapshot_json) "
                "ON CONFLICT(account_id, snapshot_id) DO UPDATE SET "
                "  captured_at = excluded.captured_at, "
                "  snapshot_json = excluded.snapshot_json",
                row,
            )
            self.conn.commit()
        except IntegrityError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:integrity:ks_snapshots:{e}")
        except OperationalError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:locked:ks_snapshots:{e}")
        except DatabaseError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:corrupt:ks_snapshots:{e}")
        return Ok(None)

    def record(self, snapshot: AuditSnapshot) -> None:
        """``SnapshotSink`` Protocol conformance. Any persistence
        failure here is a programmer-error / disk-failure invariant
        (we cannot proceed without an audit row on a KS transition),
        so we panic — matching ``FileSnapshotSink``'s implicit contract
        that a half-written audit is worse than a crash."""
        match self.write(snapshot):
            case Ok(_):
                return
            case Err(reason):
                raise RuntimeError(f"KillSwitchSnapshotRepository.record failed: {reason}")

    def get(self, snapshot_id: SnapshotId) -> Result[AuditSnapshot, str]:
        try:
            cursor = self.conn.execute(
                "SELECT * FROM ks_snapshots "
                "WHERE account_id = ? AND snapshot_id = ?",
                (str(self.account_id), str(snapshot_id)),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:ks_snapshots:read:{e}")
        if row is None:
            return Err(f"persistence:not_found:ks_snapshots:{snapshot_id}")
        return Ok(row_to_audit_snapshot(dict(row)))

    def list_in_window(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Result[tuple[AuditSnapshot, ...], str]:
        """C9 — postmortem timeline query.

        Returns every snapshot whose ``captured_at`` falls in the
        closed ``[since, until]`` window, ordered by
        ``captured_at ASC`` (timeline order — earliest first).
        Both bounds are optional: ``since=None`` ⇒ no lower bound;
        ``until=None`` ⇒ no upper bound.

        The query is scoped to ``self.account_id`` (multi-account
        isolation per REQ_F_PER_009). Operators querying a
        different account construct a separate repository with
        the right ``account_id``.
        """
        clauses = ["account_id = ?"]
        params: list[object] = [str(self.account_id)]
        if since is not None:
            clauses.append("captured_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("captured_at <= ?")
            params.append(until.isoformat())
        sql = (
            "SELECT * FROM ks_snapshots "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY captured_at ASC, snapshot_id ASC"
        )
        try:
            cursor = self.conn.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:ks_snapshots:list:{e}")
        return Ok(tuple(row_to_audit_snapshot(dict(r)) for r in raw_rows))


def _safe_rollback(conn: Connection) -> None:
    try:
        conn.rollback()
    except DatabaseError:
        pass
