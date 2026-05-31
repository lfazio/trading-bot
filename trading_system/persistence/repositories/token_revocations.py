"""``OperatorTokenRevocationRepository`` — CR-024 persistence.

The SQLite ``operator_token_revocations`` table is the source
of truth for which ``(account_id, jti)`` tuples are revoked.
``AccountScopedTokenVerifier.verify`` (REQ_F_TOK_002 /
REQ_SDD_TOK_002) consults this repository's ``is_revoked``
on every verify — there is no in-memory cache layer. The
SELECT-on-every-call shape keeps the design simple and is
correct under SQLite WAL semantics: committed revocations
in one connection are immediately visible to readers on
other connections (same host, shared SQLite file).

This is what gives single-host multi-process deployments
(multiple webapp workers behind a reverse proxy) the
cross-process revocation propagation guarantee without any
extra channel — every worker reads through the same WAL
log. Multi-host deployments are out of scope for v1
(SQLite is single-host); the future-CR path for multi-host
revocation propagation would add an SSE / database-NOTIFY
channel + a process-local cache.

REQ refs:
- REQ_F_TOK_002 — TokenRevocationList persisted.
- REQ_NF_TOK_001 — replay determinism across runs.
- REQ_F_PER_002 — repository per aggregate root.
- REQ_F_PER_003 — explicit transactions; no partial writes.
- REQ_F_PER_009 — every row carries ``account_id``.
- REQ_SDS_PER_002 — closed ``Err`` category set at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
)
from trading_system.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class TokenRevocation:
    """One row of the ``operator_token_revocations`` table."""

    account_id: AccountId
    jti: str
    revoked_at: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.jti.strip():
            raise ValueError("TokenRevocation.jti must be non-empty")


@dataclass(slots=True)
class OperatorTokenRevocationRepository:
    """SQLite-backed append-only revocation log.

    Re-revoking the same ``(account_id, jti)`` is idempotent —
    the repository swallows the integrity error and returns
    ``Ok(None)`` so the operator can replay the revocation flow
    without diff-checking against existing rows.
    """

    conn: Connection

    def revoke(
        self,
        *,
        account_id: AccountId,
        jti: str,
        reason: str = "",
        now: datetime | None = None,
    ) -> Result[None, str]:
        """Insert one revocation row. Idempotent: a duplicate
        ``(account_id, jti)`` SHALL NOT surface an Err — the
        revocation is already in effect."""
        if not jti.strip():
            return Err("persistence:bad_input:revoke:empty_jti")
        revoked_at = (now or datetime.now(tz=UTC)).isoformat()
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                "INSERT INTO operator_token_revocations "
                "(account_id, jti, revoked_at, reason) "
                "VALUES (?, ?, ?, ?)",
                (str(account_id), jti, revoked_at, reason),
            )
            self.conn.commit()
        except IntegrityError:
            # Idempotent — the row already exists; revocation is
            # already in effect. Roll back to release the lock.
            self._safe_rollback()
            return Ok(None)
        except DatabaseError as e:
            self._safe_rollback()
            return Err(f"persistence:corrupt:operator_token_revocations:write:{e}")
        return Ok(None)

    def is_revoked(self, *, account_id: AccountId, jti: str) -> Result[bool, str]:
        """Single-row lookup. Lifts ``Err`` on DB failure so the
        verifier can choose between fail-closed (treat as revoked)
        and fail-open behaviour."""
        try:
            cursor = self.conn.execute(
                "SELECT 1 FROM operator_token_revocations "
                "WHERE account_id = ? AND jti = ?",
                (str(account_id), jti),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:operator_token_revocations:read:{e}")
        return Ok(row is not None)

    def list_all(
        self,
        *,
        account_id: AccountId | None = None,
    ) -> Result[tuple[TokenRevocation, ...], str]:
        """Return every revocation row sorted by ``(account_id,
        jti)`` for deterministic iteration (REQ_NF_TOK_001 / REQ_NF_PER_001).
        When ``account_id`` is provided the result is scoped to
        that account; otherwise it returns the household view."""
        try:
            if account_id is None:
                cursor = self.conn.execute(
                    "SELECT * FROM operator_token_revocations "
                    "ORDER BY account_id ASC, jti ASC"
                )
            else:
                cursor = self.conn.execute(
                    "SELECT * FROM operator_token_revocations "
                    "WHERE account_id = ? "
                    "ORDER BY jti ASC",
                    (str(account_id),),
                )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(
                f"persistence:corrupt:operator_token_revocations:read:{e}"
            )
        out: list[TokenRevocation] = []
        for row in rows:
            try:
                out.append(
                    TokenRevocation(
                        account_id=AccountId(row["account_id"]),
                        jti=row["jti"],
                        revoked_at=datetime.fromisoformat(row["revoked_at"]),
                        reason=row["reason"] or "",
                    )
                )
            except (ValueError, KeyError) as e:
                return Err(
                    f"persistence:corrupt:operator_token_revocations:parse:{e}"
                )
        return Ok(tuple(out))

    def _safe_rollback(self) -> None:
        try:
            self.conn.rollback()
        except DatabaseError:
            pass


# Documented default — the default account sentinel matches the
# rest of the persistence layer's REQ_F_PER_009 convention.
DEFAULT_REVOCATION_ACCOUNT: AccountId = DEFAULT_ACCOUNT_ID
