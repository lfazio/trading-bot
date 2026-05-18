"""``EmailNotificationChannel`` tests — CR-001 Phase B.

REQ refs:
- REQ_F_NOT_001 / REQ_F_NOT_002 — channel conformance + SMTP
  bundled adapter.
- REQ_NF_NOT_002 — canonical body determinism.
- REQ_NF_NOT_003 / REQ_SDD_NOT_007 — env-var credential discipline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import (
    AccountId,
    InstrumentId,
    SnapshotId,
)
from trading_system.models.instrument import InstrumentClass
from trading_system.models.money import Currency, Money
from trading_system.models.safety import KillSwitchState
from trading_system.models.trading import Side
from trading_system.notifications.channels.email import (
    DEFAULT_PASSWORD_ENV,
    EmailNotificationChannel,
    render_email,
)
from trading_system.notifications.payloads import (
    AnomalyAlert,
    Error,
    KillSwitchEvent,
    Summary,
    TradeApprovalRequest,
)
from trading_system.result import Err


_AT = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
_ACCOUNT = AccountId("default")


def _channel(**overrides) -> EmailNotificationChannel:  # type: ignore[no-untyped-def]
    defaults = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "user": "trading-bot@example.com",
        "from_addr": "trading-bot@example.com",
        "recipients": ["ops@example.com"],
    }
    defaults.update(overrides)
    return EmailNotificationChannel(**defaults)


# ---------------------------------------------------------------------------
# Construction invariants
# ---------------------------------------------------------------------------


def test_default_password_env_is_documented_name() -> None:
    assert DEFAULT_PASSWORD_ENV == "TRADING_BOT_SMTP_PASSWORD"


def test_constructor_rejects_empty_host() -> None:
    with pytest.raises(ValueError, match="smtp_host"):
        _channel(smtp_host="")


def test_constructor_rejects_port_out_of_range() -> None:
    with pytest.raises(ValueError, match="smtp_port"):
        _channel(smtp_port=0)


def test_constructor_rejects_empty_recipients() -> None:
    with pytest.raises(ValueError, match="recipients"):
        _channel(recipients=[])


def test_constructor_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        _channel(timeout_seconds=0)


def test_constructor_rejects_empty_password_env() -> None:
    with pytest.raises(ValueError, match="password_env"):
        _channel(password_env="")


# ---------------------------------------------------------------------------
# Credential safety — REQ_NF_NOT_003 / REQ_SDD_NOT_007
# ---------------------------------------------------------------------------


def test_repr_does_not_leak_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_BOT_SMTP_PASSWORD", "super-secret-password")
    channel = _channel()
    rendered = repr(channel)
    assert "super-secret-password" not in rendered


def test_deliver_returns_err_when_password_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRADING_BOT_SMTP_PASSWORD", raising=False)
    channel = _channel()
    match channel.deliver(_anomaly()):
        case Err(reason):
            assert reason.startswith("notifications:email:password_env_unset:")
        case _:
            raise AssertionError("expected Err on missing env var")


# ---------------------------------------------------------------------------
# Plain-text rendering — TC_NOT_001 conformance
# ---------------------------------------------------------------------------


def test_render_kill_switch_subject_includes_severity_and_trigger() -> None:
    event = KillSwitchEvent(
        snapshot_id=SnapshotId("snap-1"),
        state_from=KillSwitchState.ACTIVE,
        state_to=KillSwitchState.KILL,
        trigger_code="financial:household_drawdown:kill",
        severity="KILL",
        summary="household drawdown 0.20 >= 0.15",
    )
    subject, body = render_email(event)
    assert "KILL" in subject
    assert "financial:household_drawdown:kill" in subject
    assert "snap-1" in body
    assert "household drawdown 0.20" in body


def test_render_trade_approval_subject_and_body() -> None:
    approval = TradeApprovalRequest(
        request_id="req-123",
        account_id=_ACCOUNT,
        instrument=InstrumentId("ASML.AS"),
        side=Side.BUY,
        quantity=Decimal("10"),
        expected_loss=Money(Decimal("250.00"), Currency.EUR),
        rationale_digest="yield>4.5; payout<70",
        requested_at=_AT,
        expires_at=datetime(2026, 5, 18, 12, 15, tzinfo=UTC),
    )
    subject, body = render_email(approval)
    assert "ASML.AS" in subject
    assert "10" in subject
    assert "yield>4.5" in body
    assert "req-123" in body


def test_render_summary_with_exposure() -> None:
    summary = Summary(
        schedule="daily",
        account_id=_ACCOUNT,
        as_of=_AT,
        equity_after_tax=Money(Decimal("12345.67"), Currency.EUR),
        exposure={InstrumentClass.STOCK: Decimal("0.6")},
    )
    subject, body = render_email(summary)
    assert "Daily summary" in body
    assert "12345.67" in body
    assert "stock" in body.lower()


def test_render_anomaly_includes_message_and_code() -> None:
    alert = _anomaly()
    subject, body = render_email(alert)
    assert "execution:reject" in subject
    assert alert.message in body
    assert "WARN" in body


def test_render_error_format() -> None:
    err = Error(
        code="config:io:missing",
        detail="config/notifications.yaml not found",
        at=_AT,
    )
    subject, body = render_email(err)
    assert "config:io:missing" in subject
    assert "config/notifications.yaml not found" in body


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_render_is_byte_identical_across_calls() -> None:
    payload = _anomaly()
    a = render_email(payload)
    b = render_email(payload)
    assert a == b


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _anomaly() -> AnomalyAlert:
    return AnomalyAlert(
        code="execution:reject",
        severity="WARN",
        account_id=_ACCOUNT,
        message="broker rejected order id=...",
        at=_AT,
    )
