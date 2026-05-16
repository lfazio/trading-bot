"""Tests for ``ApprovalGate`` (REQ_F_NOT_004, REQ_F_NOT_005,
REQ_SDS_NOT_003, REQ_SDD_NOT_003)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.models.identifiers import AccountId, InstrumentId
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side
from trading_system.notifications.approval import (
    ApprovalGate,
    MemoryResponseInbox,
    ResponseInbox,
    operator_token_hash,
)
from trading_system.notifications.channels.local_log import (
    MemoryNotificationChannel,
)
from trading_system.notifications.fanout import (
    NotificationFanOut,
    RetryPolicy,
)
from trading_system.notifications.payloads import (
    ApprovalResponse,
    TradeApprovalRequest,
)
from trading_system.result import Err, Ok


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _verifier(secret: bytes = b"shh") -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(
        secret=secret,
        ttl_seconds=300,
        _clock=lambda: _NOW,
    )


def _request(account: str = "alpha") -> TradeApprovalRequest:
    return TradeApprovalRequest(
        request_id="req-1",
        account_id=AccountId(account),
        instrument=InstrumentId("ASML.AS"),
        side=Side.BUY,
        quantity=Decimal("10"),
        expected_loss=Money(Decimal("250"), Currency.EUR),
        rationale_digest="dividend yield 5.2% > threshold 4.5%",
        requested_at=_NOW,
        expires_at=_NOW + timedelta(seconds=60),
    )


def _gate(
    *,
    inbox: ResponseInbox,
    verifier: AccountScopedTokenVerifier | None = None,
    timeout_seconds: int = 60,
    now_func=None,
) -> tuple[ApprovalGate, MemoryNotificationChannel]:
    ch = MemoryNotificationChannel()
    fan = NotificationFanOut(
        channels=(ch,),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
        sleep=lambda _t: None,
    )
    gate = ApprovalGate(
        fanout=fan,
        verifier=verifier or _verifier(),
        inbox=inbox,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=0.0,
        now=now_func or (lambda: _NOW),
        sleep=lambda _t: None,
    )
    return gate, ch


# ---------------------------------------------------------------------------
# MemoryResponseInbox conformance
# ---------------------------------------------------------------------------


def test_memory_response_inbox_satisfies_protocol() -> None:
    assert isinstance(MemoryResponseInbox(), ResponseInbox)


def test_memory_response_inbox_records_and_polls() -> None:
    inbox = MemoryResponseInbox()
    resp = ApprovalResponse(
        request_id="req-1",
        approved=True,
        operator_token="tok",
        responded_at=_NOW,
    )
    inbox.record(resp)
    # Some(resp) for a known id; Nothing() for an unknown one.
    seen = inbox.poll("req-1")
    assert seen.is_some()
    assert inbox.poll("ghost").is_none()


# ---------------------------------------------------------------------------
# Default-deny on timeout (REQ_F_NOT_004)
# ---------------------------------------------------------------------------


def test_timeout_returns_default_deny() -> None:
    """An empty inbox + a non-advancing clock means the gate hits
    the deadline immediately and returns the timeout Err."""
    inbox = MemoryResponseInbox()
    # ``now`` returns a value past the deadline so the loop exits
    # before the first poll.
    gate, _ch = _gate(
        inbox=inbox,
        timeout_seconds=0,  # deadline == now ⇒ loop exits immediately
    )
    match gate.evaluate(_request()):
        case Err(reason):
            assert reason == "notifications:approval_timeout:req-1"
        case _:
            raise AssertionError("expected Err")


def test_dispatch_fires_on_evaluate() -> None:
    """The gate emits the request through the fan-out before
    polling (REQ_F_NOT_004)."""
    inbox = MemoryResponseInbox()
    gate, ch = _gate(inbox=inbox, timeout_seconds=0)
    gate.evaluate(_request())
    assert len(ch.delivered) == 1


# ---------------------------------------------------------------------------
# Happy path — valid signature, approved=True (REQ_F_NOT_005)
# ---------------------------------------------------------------------------


def test_valid_response_approved_returns_ok() -> None:
    verifier = _verifier()
    token = verifier.issue(account_id="alpha", now=_NOW)
    inbox = MemoryResponseInbox()
    inbox.record(
        ApprovalResponse(
            request_id="req-1",
            approved=True,
            operator_token=token,
            responded_at=_NOW,
        )
    )
    gate, _ch = _gate(inbox=inbox, verifier=verifier)
    match gate.evaluate(_request()):
        case Ok(resp):
            assert resp.approved
            assert resp.request_id == "req-1"
        case Err(reason):
            raise AssertionError(reason)


# ---------------------------------------------------------------------------
# Explicit denial (REQ_F_NOT_004)
# ---------------------------------------------------------------------------


def test_valid_response_not_approved_returns_denied() -> None:
    verifier = _verifier()
    token = verifier.issue(account_id="alpha", now=_NOW)
    inbox = MemoryResponseInbox()
    inbox.record(
        ApprovalResponse(
            request_id="req-1",
            approved=False,
            operator_token=token,
            responded_at=_NOW,
        )
    )
    gate, _ch = _gate(inbox=inbox, verifier=verifier)
    match gate.evaluate(_request()):
        case Err(reason):
            assert reason == "notifications:approval_denied:req-1"
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# Token validation (REQ_F_NOT_005 / REQ_SDD_ACC_007)
# ---------------------------------------------------------------------------


def test_invalid_token_returns_registry_err() -> None:
    inbox = MemoryResponseInbox()
    inbox.record(
        ApprovalResponse(
            request_id="req-1",
            approved=True,
            operator_token="bogus-token",
            responded_at=_NOW,
        )
    )
    gate, _ch = _gate(inbox=inbox)
    match gate.evaluate(_request()):
        case Err(reason):
            assert reason == "registry:token_invalid"
        case _:
            raise AssertionError("expected Err")


def test_token_for_wrong_account_rejected() -> None:
    """REQ_SDD_ACC_007 — a token signed for ``beta`` SHALL be
    rejected when the request targets ``alpha``."""
    verifier = _verifier()
    token_for_beta = verifier.issue(account_id="beta", now=_NOW)
    inbox = MemoryResponseInbox()
    inbox.record(
        ApprovalResponse(
            request_id="req-1",
            approved=True,
            operator_token=token_for_beta,
            responded_at=_NOW,
        )
    )
    gate, _ch = _gate(inbox=inbox, verifier=verifier)
    match gate.evaluate(_request(account="alpha")):
        case Err(reason):
            assert reason == "registry:token_invalid"
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# operator_token_hash helper
# ---------------------------------------------------------------------------


def test_operator_token_hash_is_sha256() -> None:
    assert (
        operator_token_hash("hello")
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_operator_token_hash_is_deterministic() -> None:
    assert operator_token_hash("x") == operator_token_hash("x")
    assert operator_token_hash("x") != operator_token_hash("y")
