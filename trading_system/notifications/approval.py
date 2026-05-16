"""``ApprovalGate`` — operator approval for high-stakes proposals.

Sits between per-account risk and order submission for proposals
whose ``expected_loss`` exceeds the configured threshold. Emits a
``TradeApprovalRequest`` through the fan-out, polls the response
inbox until ``timeout_seconds``, and validates the operator's HMAC
token (REQ_F_NOT_005). **Default-deny** on timeout (REQ_F_NOT_004).

The HMAC verifier here mirrors
``trading_system.accounts.token_verifier.AccountScopedTokenVerifier``
— same signing format, same account-id-claim binding. The audit
row stores ``sha256(token)`` only; the raw token is never persisted.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.models.identifiers import AccountId
from trading_system.notifications.fanout import NotificationFanOut
from trading_system.notifications.payloads import (
    ApprovalResponse,
    TradeApprovalRequest,
)
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@runtime_checkable
class ResponseInbox(Protocol):
    """Source the gate polls for operator responses.

    Phase A wires an in-memory implementation for tests + the demo;
    Phase B replaces it with a CR-008-backed
    ``TradeApprovalAuditRepository.poll`` or a CR-004 web-UI inbox.
    """

    def poll(self, request_id: str) -> Option[ApprovalResponse]: ...


@dataclass(slots=True)
class MemoryResponseInbox:
    """Test double + demo backend; tests use the ``record`` helper
    to inject responses keyed by request_id."""

    _by_request_id: dict[str, ApprovalResponse] = field(default_factory=dict)

    def record(self, response: ApprovalResponse) -> None:
        self._by_request_id[response.request_id] = response

    def poll(self, request_id: str) -> Option[ApprovalResponse]:
        existing = self._by_request_id.get(request_id)
        if existing is None:
            return Nothing()
        return Some(existing)


@dataclass(slots=True)
class ApprovalGate:
    """Configured per-deployment; the runtime constructs one
    instance and passes it to the trade-execution path."""

    fanout: NotificationFanOut
    verifier: AccountScopedTokenVerifier
    inbox: ResponseInbox
    timeout_seconds: int = 60
    poll_interval_seconds: float = 0.05
    # Injectable clock + sleep for tests.
    now: Callable[[], datetime] = field(default_factory=lambda: _default_now)
    sleep: Callable[[float], None] = field(default_factory=lambda: time.sleep)

    def evaluate(
        self,
        request: TradeApprovalRequest,
    ) -> Result[ApprovalResponse, str]:
        """Dispatch the request + poll the inbox until the deadline.

        Returns ``Ok(response)`` on a valid operator approval;
        ``Err("notifications:approval_timeout:<id>")`` on timeout
        (REQ_F_NOT_004 default-deny); ``Err("notifications:approval_denied:<id>")``
        on an explicit operator denial;
        ``Err("registry:token_invalid")`` on signature/claim
        mismatch (REQ_F_NOT_005).
        """
        # Fire-and-forget — the fan-out's retry policy handles
        # transient channel failures; the gate's job is to poll the
        # inbox, not to verify delivery.
        self.fanout.dispatch(request)

        deadline = self.now() + timedelta(seconds=self.timeout_seconds)
        while self.now() < deadline:
            polled = self.inbox.poll(request.request_id)
            match polled:
                case Some(response):
                    return self._verify(response, request)
                case Nothing():
                    self.sleep(self.poll_interval_seconds)
        return Err(f"notifications:approval_timeout:{request.request_id}")

    def _verify(
        self, response: ApprovalResponse, request: TradeApprovalRequest
    ) -> Result[ApprovalResponse, str]:
        # HMAC + account-id claim verification — REQ_F_NOT_005 /
        # REQ_SDD_ACC_007. The same closed Err category as the
        # registry-promotion path so call sites pattern-match the
        # same string.
        if not self.verifier.verify(
            response.operator_token, account_id=str(request.account_id)
        ):
            return Err("registry:token_invalid")
        if not response.approved:
            return Err(f"notifications:approval_denied:{request.request_id}")
        return Ok(response)


def operator_token_hash(token: str) -> str:
    """Helper for the Phase-B audit-row persistence — the audit
    repository stores ``sha256(token)`` only (REQ_F_NOT_005 /
    REQ_SDD_PER_005 family)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _default_now() -> datetime:
    from datetime import UTC  # local import to keep top-level light

    return datetime.now(tz=UTC)


# Re-export the in-memory inbox so Phase-B callers can construct a
# fan-out + gate without dependency on the test-only helper file.
__all__ = [
    "ApprovalGate",
    "MemoryResponseInbox",
    "ResponseInbox",
    "operator_token_hash",
]


# Avoid the unused-import warning on ``AccountId`` — keeps the type
# accessible for callers that want to construct AccountIds from
# string literals without re-importing.
_ = AccountId  # noqa: F841
