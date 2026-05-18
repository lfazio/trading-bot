"""``TradeApprovalAuditRepository`` — CR-001 Phase B persistence.

REQ refs:
- REQ_F_NOT_004 / REQ_F_NOT_005 — operator approval requests +
  responses live in an audit trail. Raw operator tokens NEVER
  persisted; only their SHA-256 hashes land in
  ``operator_token_hash`` (mirrors the existing
  ``registry_promotions.promoter_token_hash`` discipline).
- REQ_NF_NOT_003 — minimum-necessary content; the rationale is
  the operator-supplied digest, not the full ``TradeRationale``
  payload.
- REQ_F_PER_002 — one repository per aggregate root.
- REQ_F_PER_003 — explicit transactions; no partial writes.
- REQ_F_PER_009 — every row carries ``account_id`` (defaults to
  ``DEFAULT_ACCOUNT_ID`` for single-account deployments).
- REQ_SDS_PER_002 — closed ``Err`` category set at the boundary.

Schema lives in ``persistence/migrations/0003_approvals.sql``. The
table is append-only at the per-(account_id, request_id) grain;
``record_request`` inserts the request row and ``record_response``
inserts the matching response row. Duplicate request_id surfaces
as ``persistence:integrity:approval_requests:...``.

The repository is intentionally minimal: it stores the audit
trail. The decision-flow gate (``ApprovalGate.evaluate``) lives in
``notifications/approval.py`` and DOES NOT know about this
repository — the operator wires the audit by calling
``record_request`` + ``record_response`` around the gate at the
trade-decision call site.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId
from trading_system.notifications.approval import operator_token_hash
from trading_system.notifications.payloads import (
    ApprovalResponse,
    TradeApprovalRequest,
)
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
)
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@dataclass(slots=True)
class TradeApprovalAuditRepository:
    """SQLite-backed audit trail for the trade-approval gate."""

    conn: Connection

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record_request(
        self,
        request: TradeApprovalRequest,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Insert one ``approval_requests`` row. Duplicate
        ``(account_id, request_id)`` surfaces as the categorised
        integrity Err."""
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                """
                INSERT INTO approval_requests (
                    account_id, request_id, instrument_id, side,
                    quantity, expected_loss_amount, expected_loss_currency,
                    rationale_digest, requested_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(account_id),
                    request.request_id,
                    str(request.instrument),
                    request.side.value,
                    str(request.quantity),
                    str(request.expected_loss.amount),
                    request.expected_loss.currency.value,
                    request.rationale_digest,
                    request.requested_at.isoformat(),
                    request.expires_at.isoformat(),
                ),
            )
            self.conn.commit()
        except IntegrityError as e:
            self.conn.rollback()
            return Err(
                f"persistence:integrity:approval_requests:duplicate:{request.request_id}: {e}"
            )
        except DatabaseError as e:
            self.conn.rollback()
            return Err(f"persistence:corrupt:approval_requests:write:{e}")
        return Ok(None)

    def record_response(
        self,
        response: ApprovalResponse,
        *,
        operator_id: str,
        rejection_reason: str = "",
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Insert one ``approval_responses`` row. The raw operator
        token from the response is hashed at this boundary; only
        the hash is persisted (REQ_F_NOT_005 / REQ_NF_NOT_003).

        ``operator_id`` is passed explicitly because the
        ``ApprovalResponse`` payload carries only the bound token
        (the operator identity is established via the HMAC claim).
        The audit row records the operator-supplied id for human-
        readable triage.

        ``rejection_reason`` is the categorised string the gate
        produced if the response was a denial (or a synthetic
        timeout / token-invalid surface). Empty for approvals.
        """
        token_hash = operator_token_hash(response.operator_token)
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                """
                INSERT INTO approval_responses (
                    account_id, request_id, approved, operator_id,
                    operator_token_hash, responded_at, rejection_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(account_id),
                    response.request_id,
                    1 if response.approved else 0,
                    operator_id,
                    token_hash,
                    response.responded_at.isoformat(),
                    rejection_reason,
                ),
            )
            self.conn.commit()
        except IntegrityError as e:
            self.conn.rollback()
            return Err(
                f"persistence:integrity:approval_responses:duplicate_or_missing:{response.request_id}: {e}"
            )
        except DatabaseError as e:
            self.conn.rollback()
            return Err(f"persistence:corrupt:approval_responses:write:{e}")
        return Ok(None)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_request(
        self,
        request_id: str,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[Option[dict[str, object]], str]:
        """Read one request row by id. Returns a flat dict mapping
        the column names to their persisted values — kept as a
        dict rather than the original ``TradeApprovalRequest``
        payload so the audit row stays read-cheap (no payload
        construction)."""
        try:
            cursor = self.conn.execute(
                "SELECT * FROM approval_requests "
                "WHERE account_id = ? AND request_id = ?",
                (str(account_id), request_id),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:approval_requests:read:{e}")
        if row is None:
            return Ok(Nothing())
        return Ok(Some(dict(row)))

    def get_response(
        self,
        request_id: str,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[Option[dict[str, object]], str]:
        """Read one response row by request_id."""
        try:
            cursor = self.conn.execute(
                "SELECT * FROM approval_responses "
                "WHERE account_id = ? AND request_id = ?",
                (str(account_id), request_id),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:approval_responses:read:{e}")
        if row is None:
            return Ok(Nothing())
        return Ok(Some(dict(row)))

    def verify_token(
        self,
        request_id: str,
        raw_token: str,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[bool, str]:
        """Check whether ``raw_token`` matches the persisted hash for
        ``request_id`` (REQ_F_NOT_005 family).

        Returns ``Ok(True)`` if the hash matches, ``Ok(False)``
        otherwise. ``Err`` only on persistence-level failure.
        """
        match self.get_response(request_id, account_id=account_id):
            case Ok(Some(row)):
                expected_hash = row["operator_token_hash"]
                return Ok(operator_token_hash(raw_token) == expected_hash)
            case Ok(Nothing()):
                return Ok(False)
            case Err(reason):
                return Err(reason)
        return Ok(False)
