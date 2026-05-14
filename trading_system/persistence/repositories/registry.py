"""``RegistryRepository`` — durable strategy registry with operator-
gated promotion (CR-008 Phase 5 / REQ_F_PER_006 / REQ_SDD_PER_005).

Responsibilities:

- ``store(entry)`` — insert or update an entry. Mirrors
  ``Registry.store``'s ``validated_immutable`` semantics: a validated
  row SHALL NOT be overwritten by another validated row under the same
  ``(account_id, strategy_id)`` key.
- ``get(strategy_id)`` — read a single entry (``Some`` / ``Nothing``).
- ``list_validated()`` — every validated entry for the account, sorted
  by id.
- ``request_promotion(strategy_id, token, ...)`` — atomic
  ``UPDATE strategy_registry SET validated=1`` + ``INSERT INTO
  registry_promotions`` after verifying the operator HMAC via the same
  ``OperatorTokenVerifier`` that backs the KS recovery flow
  (REQ_S_KS_009). The raw token SHALL NEVER touch persistent storage —
  only its SHA-256 hash is recorded.

Error categories surfaced to callers belong to the closed set defined
in REQ_SDD_PER_003 plus ``registry:*`` codes mirrored from the
in-memory ``Registry``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId, StrategyId
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
    OperationalError,
)
from trading_system.persistence.mappers import (
    registry_entry_to_row,
    row_to_registry_entry,
)
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.safety.recovery import OperatorTokenVerifier
from trading_system.strategy_lab.registry import RegistryEntry


@dataclass(slots=True)
class RegistryRepository:
    """SQLite-backed implementation of the strategy registry."""

    conn: Connection

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(
        self,
        strategy_id: StrategyId,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[Option[RegistryEntry], str]:
        try:
            cursor = self.conn.execute(
                "SELECT * FROM strategy_registry "
                "WHERE account_id = ? AND strategy_id = ?",
                (str(account_id), str(strategy_id)),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:strategy_registry:read:{e}")
        if row is None:
            return Ok(Nothing())
        return Ok(Some(row_to_registry_entry(dict(row))))

    def list_validated(
        self,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[tuple[RegistryEntry, ...], str]:
        try:
            cursor = self.conn.execute(
                "SELECT * FROM strategy_registry "
                "WHERE account_id = ? AND validated = 1 "
                "ORDER BY strategy_id ASC",
                (str(account_id),),
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:strategy_registry:read:{e}")
        return Ok(tuple(row_to_registry_entry(dict(r)) for r in rows))

    def list_experimental(
        self,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[tuple[RegistryEntry, ...], str]:
        try:
            cursor = self.conn.execute(
                "SELECT * FROM strategy_registry "
                "WHERE account_id = ? AND validated = 0 "
                "ORDER BY strategy_id ASC",
                (str(account_id),),
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:strategy_registry:read:{e}")
        return Ok(tuple(row_to_registry_entry(dict(r)) for r in rows))

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def store(
        self,
        entry: RegistryEntry,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Insert or update ``entry``. Mirrors ``Registry.store``:
        a validated row cannot be overwritten by another validated row
        under the same id."""
        existing_q = self.get(entry.strategy_id, account_id=account_id)
        match existing_q:
            case Err(e):
                return Err(e)
            case Ok(Some(existing)):
                if existing.validated and entry.validated:
                    return Err(f"registry:validated_immutable:{entry.strategy_id}")
            case Ok(_):
                pass

        row = registry_entry_to_row(entry, str(account_id))
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                "INSERT INTO strategy_registry "
                "(account_id, strategy_id, git_sha, config_hash, seed, "
                " metrics_json, validated, created_at, notes) "
                "VALUES (:account_id, :strategy_id, :git_sha, :config_hash, "
                "        :seed, :metrics_json, :validated, :created_at, :notes) "
                "ON CONFLICT(account_id, strategy_id) DO UPDATE SET "
                "  git_sha = excluded.git_sha, "
                "  config_hash = excluded.config_hash, "
                "  seed = excluded.seed, "
                "  metrics_json = excluded.metrics_json, "
                "  validated = excluded.validated, "
                "  created_at = excluded.created_at, "
                "  notes = excluded.notes",
                row,
            )
            self.conn.commit()
        except IntegrityError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:integrity:strategy_registry:{e}")
        except OperationalError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:locked:strategy_registry:{e}")
        except DatabaseError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:corrupt:strategy_registry:{e}")
        return Ok(None)

    def request_promotion(
        self,
        strategy_id: StrategyId,
        token: str,
        *,
        verifier: OperatorTokenVerifier,
        operator_id: str,
        rationale: str,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Verify the operator HMAC, then atomically flip ``validated``
        and append a ``registry_promotions`` audit row.

        Per REQ_SDD_PER_005, the raw token is never written; only its
        SHA-256 hash. The token check runs **before** any DB write so
        an invalid token has zero side effects.
        """
        if not verifier.verify(token):
            return Err("registry:token_invalid")

        existing_q = self.get(strategy_id, account_id=account_id)
        match existing_q:
            case Err(e):
                return Err(e)
            case Ok(Nothing()):
                return Err(f"registry:not_found:{strategy_id}")
            case Ok(Some(existing)):
                if existing.validated:
                    return Err(f"registry:already_validated:{strategy_id}")

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        promoted_at = datetime.now(tz=UTC).isoformat()

        try:
            self.conn.begin_immediate()
            self.conn.execute(
                "UPDATE strategy_registry SET validated = 1 "
                "WHERE account_id = ? AND strategy_id = ?",
                (str(account_id), str(strategy_id)),
            )
            self.conn.execute(
                "INSERT INTO registry_promotions "
                "(account_id, strategy_id, promoted_by, promoted_at, "
                " promoter_token_hash, promotion_rationale) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(account_id),
                    str(strategy_id),
                    operator_id,
                    promoted_at,
                    token_hash,
                    rationale,
                ),
            )
            self.conn.commit()
        except IntegrityError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:integrity:registry_promotions:{e}")
        except OperationalError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:locked:registry_promotions:{e}")
        except DatabaseError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:corrupt:registry_promotions:{e}")
        return Ok(None)

    def promotion_audit(
        self,
        strategy_id: StrategyId,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[tuple[dict, ...], str]:
        """Audit reader — returns every promotion row for ``strategy_id``
        in chronological order. The ``promoter_token_hash`` is exposed
        but the raw token never is (it was never stored)."""
        try:
            cursor = self.conn.execute(
                "SELECT promoted_by, promoted_at, promoter_token_hash, "
                "       promotion_rationale "
                "FROM registry_promotions "
                "WHERE account_id = ? AND strategy_id = ? "
                "ORDER BY promoted_at ASC",
                (str(account_id), str(strategy_id)),
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:registry_promotions:read:{e}")
        return Ok(tuple(dict(r) for r in rows))


def _safe_rollback(conn: Connection) -> None:
    try:
        conn.rollback()
    except DatabaseError:
        pass
