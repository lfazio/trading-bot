"""Closed ``NotificationPayload`` union — REQ_F_NOT_001 / REQ_NF_NOT_003.

Every payload is a ``@dataclass(frozen=True, slots=True)`` so the
fan-out can hash + serialise rows deterministically. The string
fields enforce non-empty invariants at construction; numeric fields
enforce sign / bounds. Privacy (REQ_NF_NOT_003) is enforced by the
minimum-necessary content rule — no raw operator tokens, no full
trade rationales (digest only; full text lives in CR-015's
persistence).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from trading_system.models.identifiers import (
    AccountId,
    InstrumentId,
    SnapshotId,
)
from trading_system.models.instrument import InstrumentClass
from trading_system.models.money import Money
from trading_system.models.safety import KillSwitchState
from trading_system.models.trading import Side


# ---------------------------------------------------------------------------
# KillSwitchEvent — REQ_F_NOT_003
# ---------------------------------------------------------------------------


KillSwitchSeverity = Literal["DEGRADE", "KILL", "RECOVERY"]


@dataclass(frozen=True, slots=True)
class KillSwitchEvent:
    snapshot_id: SnapshotId
    state_from: KillSwitchState
    state_to: KillSwitchState
    trigger_code: str
    severity: KillSwitchSeverity
    summary: str

    def __post_init__(self) -> None:
        if not str(self.snapshot_id).strip():
            raise ValueError("KillSwitchEvent.snapshot_id must be non-empty")
        if not self.trigger_code.strip():
            raise ValueError("KillSwitchEvent.trigger_code must be non-empty")
        if not self.summary.strip():
            raise ValueError("KillSwitchEvent.summary must be non-empty")


# ---------------------------------------------------------------------------
# TradeApprovalRequest / ApprovalResponse — REQ_F_NOT_004 / REQ_F_NOT_005
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradeApprovalRequest:
    """Operator approval request for a high-stakes proposal.

    ``request_id`` SHALL be deterministic — SHA256 over
    ``(account_id, proposal_hash)`` — so the inbox can dedupe + the
    audit trail keys cleanly (REQ_F_NOT_004).
    """

    request_id: str
    account_id: AccountId
    instrument: InstrumentId
    side: Side
    quantity: Decimal
    expected_loss: Money
    rationale_digest: str  # one-line; full text in persistence
    requested_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("TradeApprovalRequest.request_id must be non-empty")
        if not str(self.account_id).strip():
            raise ValueError("TradeApprovalRequest.account_id must be non-empty")
        if not str(self.instrument).strip():
            raise ValueError("TradeApprovalRequest.instrument must be non-empty")
        if self.quantity <= 0:
            raise ValueError(
                f"TradeApprovalRequest.quantity must be > 0, got {self.quantity}"
            )
        if not self.rationale_digest.strip():
            raise ValueError(
                "TradeApprovalRequest.rationale_digest must be non-empty"
            )
        if self.expires_at <= self.requested_at:
            raise ValueError(
                "TradeApprovalRequest.expires_at must be > requested_at"
            )


@dataclass(frozen=True, slots=True)
class ApprovalResponse:
    """Operator response — carries an HMAC token bound to the
    request_id + account_id (REQ_F_NOT_005). The raw token NEVER
    persists; the audit repository stores ``sha256(token)`` only.
    """

    request_id: str
    approved: bool
    operator_token: str
    responded_at: datetime

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("ApprovalResponse.request_id must be non-empty")
        if not self.operator_token.strip():
            raise ValueError("ApprovalResponse.operator_token must be non-empty")


# ---------------------------------------------------------------------------
# Summary — REQ_F_NOT_006
# ---------------------------------------------------------------------------


SummarySchedule = Literal["daily", "weekly", "monthly"]


@dataclass(frozen=True, slots=True)
class RealizationLine:
    """One row of ``Summary.top_realizations``."""

    instrument: InstrumentId
    realized_after_tax: Money
    closed_at: datetime


@dataclass(frozen=True, slots=True)
class Summary:
    schedule: SummarySchedule
    account_id: AccountId
    as_of: datetime
    equity_after_tax: Money
    exposure: Mapping[InstrumentClass, Decimal]
    top_realizations: tuple[RealizationLine, ...] = ()
    pending_milestones: tuple[str, ...] = ()
    last_improvement_digest: str = ""

    def __post_init__(self) -> None:
        if not str(self.account_id).strip():
            raise ValueError("Summary.account_id must be non-empty")
        for cls, pct in self.exposure.items():
            if not (Decimal("0") <= pct <= Decimal("1")):
                raise ValueError(
                    f"Summary.exposure[{cls}] must lie in [0, 1], got {pct}"
                )
        for line in self.top_realizations:
            if not isinstance(line, RealizationLine):
                raise TypeError(
                    f"Summary.top_realizations items must be RealizationLine, "
                    f"got {type(line).__name__}"
                )


# ---------------------------------------------------------------------------
# AnomalyAlert — REQ_F_NOT_007
# ---------------------------------------------------------------------------


AnomalySeverity = Literal["INFO", "WARN", "URGENT"]


@dataclass(frozen=True, slots=True)
class AnomalyAlert:
    code: str
    severity: AnomalySeverity
    account_id: AccountId
    message: str
    at: datetime

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("AnomalyAlert.code must be non-empty")
        if not str(self.account_id).strip():
            raise ValueError("AnomalyAlert.account_id must be non-empty")
        if not self.message.strip():
            raise ValueError("AnomalyAlert.message must be non-empty")


# ---------------------------------------------------------------------------
# Error — REQ_F_NOT_001 (closure of the payload union)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Error:
    """Out-of-band error notification.

    Used when the notification path itself fails permanently — e.g.,
    the operator's WhatsApp endpoint is rejecting messages and the
    LocalLogChannel needs to surface the failure as a structured row.
    """

    code: str
    detail: str
    at: datetime

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("Error.code must be non-empty")
        if not self.detail.strip():
            raise ValueError("Error.detail must be non-empty")


# ---------------------------------------------------------------------------
# Closed union — every payload kind the fan-out can carry
# ---------------------------------------------------------------------------


NotificationPayload = (
    KillSwitchEvent
    | TradeApprovalRequest
    | ApprovalResponse
    | Summary
    | AnomalyAlert
    | Error
)
