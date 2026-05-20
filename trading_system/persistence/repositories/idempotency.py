"""``SqliteIdempotencyStore`` — CR-004 Phase B persistence backend
(REQ_F_WEB_010 / REQ_SDD_WEB_004 / REQ_SDS_WEB_004).

Satisfies the ``trading_system.webui.idempotency.IdempotencyStore``
Protocol — drop-in replacement for ``InMemoryIdempotencyStore``
when operators set ``config/webui.yaml``'s
``idempotency_backend: persistence``.

Schema lives in ``persistence/migrations/0005_idempotency.sql``;
rows are keyed on ``(account_id, key)`` so two accounts can reuse
the same idempotency token without colliding. TTL is enforced
lazily on ``lookup`` (matches the in-memory backend's contract).

The persistence layer is the single system of record for every
mutating request's idempotency state (REQ_F_WEB_010) — there's no
parallel cache; the route layer reads + writes through this repo
exclusively when ``idempotency_backend: persistence`` is wired.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from trading_system.models.identifiers import AccountId
from trading_system.persistence.connection import Connection, DatabaseError
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


def _default_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(slots=True)
class SqliteIdempotencyStore:
    """SQLite-backed ``IdempotencyStore``.

    The constructor takes a CR-008 ``Connection`` plus the same
    ``ttl_seconds`` knob as the in-memory backend. The clock is
    injectable so determinism tests can pin "now" without touching
    the wallclock.
    """

    conn: Connection
    ttl_seconds: int = 86_400
    now: Callable[[], datetime] = field(default_factory=lambda: _default_now)

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError(
                f"SqliteIdempotencyStore.ttl_seconds must be > 0, "
                f"got {self.ttl_seconds}"
            )

    def lookup(
        self, *, account_id: AccountId, key: str
    ) -> Result[Option[str], str]:
        """REQ_SDD_WEB_004 — ``Ok(Some(prior_response_body))`` for a
        fresh entry; ``Ok(Nothing())`` if absent or beyond TTL.
        Expired entries are deleted lazily on lookup so the table
        doesn't grow unbounded between operator-run sweeps."""
        try:
            cursor = self.conn.execute(
                "SELECT body, recorded_at FROM idempotency_entries "
                "WHERE account_id = ? AND key = ?",
                (str(account_id), key),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:idempotency_entries:read:{e}")
        if row is None:
            return Ok(Nothing())
        recorded_at = datetime.fromisoformat(row["recorded_at"])
        if self.now() - recorded_at > timedelta(seconds=self.ttl_seconds):
            # Lazy TTL sweep — drop the expired row.
            try:
                self.conn.begin_immediate()
                self.conn.execute(
                    "DELETE FROM idempotency_entries "
                    "WHERE account_id = ? AND key = ?",
                    (str(account_id), key),
                )
                self.conn.commit()
            except DatabaseError as e:
                self.conn.rollback()
                return Err(
                    f"persistence:corrupt:idempotency_entries:expired_delete:{e}"
                )
            return Ok(Nothing())
        return Ok(Some(row["body"]))

    def record(
        self,
        *,
        account_id: AccountId,
        key: str,
        body: str,
        status_code: int,
    ) -> Result[None, str]:
        """REQ_SDD_WEB_004 — record a (key, body, status) tuple.
        Re-recording with the same key + IDENTICAL body is a no-op
        (idempotent); divergent body surfaces as
        ``webui:idempotency_conflict`` matching the in-memory
        backend's contract."""
        if not key.strip():
            return Err("webui:idempotency_bad_key")
        try:
            existing_cursor = self.conn.execute(
                "SELECT body FROM idempotency_entries "
                "WHERE account_id = ? AND key = ?",
                (str(account_id), key),
            )
            existing = existing_cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:idempotency_entries:read:{e}")
        if existing is not None and existing["body"] != body:
            return Err("webui:idempotency_conflict")
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                """
                INSERT INTO idempotency_entries (
                    account_id, key, body, status_code, recorded_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, key) DO UPDATE SET
                    body = excluded.body,
                    status_code = excluded.status_code,
                    recorded_at = excluded.recorded_at
                """,
                (
                    str(account_id),
                    key,
                    body,
                    int(status_code),
                    self.now().isoformat(),
                ),
            )
            self.conn.commit()
        except DatabaseError as e:
            self.conn.rollback()
            return Err(f"persistence:corrupt:idempotency_entries:write:{e}")
        return Ok(None)

    def status_code_for(
        self, *, account_id: AccountId, key: str
    ) -> int | None:
        """Read the stored status code without re-checking TTL.
        Mirrors the in-memory backend's convenience accessor;
        Phase-C may fold this into ``lookup``'s return shape."""
        try:
            cursor = self.conn.execute(
                "SELECT status_code FROM idempotency_entries "
                "WHERE account_id = ? AND key = ?",
                (str(account_id), key),
            )
            row = cursor.fetchone()
        except DatabaseError:
            return None
        if row is None:
            return None
        return int(row["status_code"])

    def sweep_expired(self, *, account_id: AccountId) -> Result[int, str]:
        """Operator-driven sweep — deletes every expired entry for
        the account. Returns the number of rows removed. Not on the
        route's hot path; intended for an out-of-band cron-style
        invocation."""
        cutoff = (self.now() - timedelta(seconds=self.ttl_seconds)).isoformat()
        try:
            self.conn.begin_immediate()
            cursor = self.conn.execute(
                "DELETE FROM idempotency_entries "
                "WHERE account_id = ? AND recorded_at < ?",
                (str(account_id), cutoff),
            )
            removed = cursor.rowcount
            self.conn.commit()
        except DatabaseError as e:
            self.conn.rollback()
            return Err(f"persistence:corrupt:idempotency_entries:sweep:{e}")
        return Ok(removed)
