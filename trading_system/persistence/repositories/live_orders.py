"""``LiveOrderRepository`` — CR-019 step 2 audit trail.

Backs the pre-submit + post-submit persistence the live runtime
performs around every `BrokerAdapter.submit` call (REQ_F_LIV_007 /
REQ_SDD_LIV_003 / REQ_SDD_LIV_006).

Two distinct transactions for pre-submit (`record_submit_intent`)
and post-submit (`record_submitted` / `record_rejected`) so the
write lock is not held across the (potentially long-running)
broker network call.

REQ refs: REQ_F_LIV_007, REQ_SDD_LIV_006, REQ_F_PER_002 / 003 / 009,
REQ_NF_PER_001 (round-trip).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId, OrderId
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
    OperationalError,
)
from trading_system.result import Err, Ok, Result


class LiveOrderStatus(StrEnum):
    """Lifecycle states for a persisted live-order row."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class LiveOrderRow:
    """One row of the ``live_orders`` table."""

    account_id: AccountId
    order_id: OrderId
    broker_selector: str
    submitted_at: datetime
    submitted_order_json: str
    corr_id: str
    status: LiveOrderStatus
    broker_order_id: str | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if not str(self.order_id).strip():
            raise ValueError("LiveOrderRow.order_id must be non-empty")
        if not self.broker_selector.strip():
            raise ValueError(
                "LiveOrderRow.broker_selector must be non-empty"
            )
        if not self.submitted_order_json.strip():
            raise ValueError(
                "LiveOrderRow.submitted_order_json must be non-empty"
            )


@dataclass(slots=True)
class LiveOrderRepository:
    """SQLite-backed live-order audit trail."""

    conn: Connection

    # ------------------------------------------------------------------
    # Pre-submit
    # ------------------------------------------------------------------

    def record_submit_intent(
        self,
        *,
        order_id: OrderId,
        account_id: AccountId,
        broker_selector: str,
        submitted_order_json: str,
        corr_id: str,
        now: datetime | None = None,
    ) -> Result[None, str]:
        """Insert a pre-submit row in ``status="pending"``. The runtime
        SHALL call this BEFORE ``broker.submit(...)`` so a crash mid-
        submit leaves a recoverable audit row."""
        if not str(order_id).strip():
            return Err("persistence:bad_input:live_orders:empty_order_id")
        if not broker_selector.strip():
            return Err("persistence:bad_input:live_orders:empty_broker_selector")
        submitted_at = (now or datetime.now(tz=UTC)).isoformat()
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                "INSERT INTO live_orders ("
                "  account_id, order_id, broker_selector, broker_order_id, "
                "  submitted_at, submitted_order_json, corr_id, status, "
                "  rejection_reason"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(account_id),
                    str(order_id),
                    broker_selector,
                    None,
                    submitted_at,
                    submitted_order_json,
                    corr_id,
                    LiveOrderStatus.PENDING.value,
                    None,
                ),
            )
            self.conn.commit()
        except IntegrityError as e:
            self._safe_rollback()
            return Err(f"persistence:integrity:live_orders:{e}")
        except OperationalError as e:
            self._safe_rollback()
            return Err(f"persistence:locked:live_orders:{e}")
        except DatabaseError as e:
            self._safe_rollback()
            return Err(f"persistence:corrupt:live_orders:{e}")
        return Ok(None)

    # ------------------------------------------------------------------
    # Post-submit
    # ------------------------------------------------------------------

    def record_submitted(
        self,
        *,
        order_id: OrderId,
        broker_order_id: str,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Flip the pre-submit row's status to ``"submitted"`` and
        populate ``broker_order_id``. The runtime SHALL call this
        after a successful `broker.submit(...)` returns Ok."""
        if not broker_order_id.strip():
            return Err(
                "persistence:bad_input:live_orders:empty_broker_order_id"
            )
        try:
            self.conn.begin_immediate()
            cursor = self.conn.execute(
                "UPDATE live_orders SET status = ?, broker_order_id = ? "
                "WHERE account_id = ? AND order_id = ?",
                (
                    LiveOrderStatus.SUBMITTED.value,
                    broker_order_id,
                    str(account_id),
                    str(order_id),
                ),
            )
            updated = cursor.rowcount
            self.conn.commit()
        except DatabaseError as e:
            self._safe_rollback()
            return Err(f"persistence:corrupt:live_orders:update:{e}")
        if updated == 0:
            return Err(
                f"persistence:not_found:live_orders:{account_id}/{order_id}"
            )
        return Ok(None)

    def record_rejected(
        self,
        *,
        order_id: OrderId,
        rejection_reason: str,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Flip the pre-submit row's status to ``"rejected"`` and
        populate ``rejection_reason``."""
        if not rejection_reason.strip():
            return Err(
                "persistence:bad_input:live_orders:empty_rejection_reason"
            )
        try:
            self.conn.begin_immediate()
            cursor = self.conn.execute(
                "UPDATE live_orders SET status = ?, rejection_reason = ? "
                "WHERE account_id = ? AND order_id = ?",
                (
                    LiveOrderStatus.REJECTED.value,
                    rejection_reason,
                    str(account_id),
                    str(order_id),
                ),
            )
            updated = cursor.rowcount
            self.conn.commit()
        except DatabaseError as e:
            self._safe_rollback()
            return Err(f"persistence:corrupt:live_orders:update:{e}")
        if updated == 0:
            return Err(
                f"persistence:not_found:live_orders:{account_id}/{order_id}"
            )
        return Ok(None)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_pending(
        self, *, account_id: AccountId = DEFAULT_ACCOUNT_ID
    ) -> Result[tuple[LiveOrderRow, ...], str]:
        """Return every row in ``status="pending"`` for the targeted
        account, sorted by ``submitted_at`` ASC. The operator's
        reconciliation panel consumes this — a non-empty result after
        a crash indicates orders whose broker side-effect is unknown."""
        try:
            cursor = self.conn.execute(
                "SELECT * FROM live_orders "
                "WHERE account_id = ? AND status = ? "
                "ORDER BY submitted_at ASC",
                (str(account_id), LiveOrderStatus.PENDING.value),
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:live_orders:read:{e}")
        return Ok(tuple(_row_to_live_order(dict(row)) for row in rows))

    def get(
        self,
        *,
        order_id: OrderId,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[LiveOrderRow | None, str]:
        """Single-row lookup by `(account_id, order_id)`."""
        try:
            cursor = self.conn.execute(
                "SELECT * FROM live_orders "
                "WHERE account_id = ? AND order_id = ?",
                (str(account_id), str(order_id)),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:live_orders:read:{e}")
        if row is None:
            return Ok(None)
        return Ok(_row_to_live_order(dict(row)))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _safe_rollback(self) -> None:
        try:
            self.conn.rollback()
        except DatabaseError:
            pass


def _row_to_live_order(row: dict) -> LiveOrderRow:
    return LiveOrderRow(
        account_id=AccountId(row["account_id"]),
        order_id=OrderId(row["order_id"]),
        broker_selector=row["broker_selector"],
        broker_order_id=row["broker_order_id"],
        submitted_at=datetime.fromisoformat(row["submitted_at"]),
        submitted_order_json=row["submitted_order_json"],
        corr_id=row["corr_id"],
        status=LiveOrderStatus(row["status"]),
        rejection_reason=row["rejection_reason"],
    )
