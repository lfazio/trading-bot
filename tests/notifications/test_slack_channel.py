"""``SlackNotificationChannel`` tests — CR-001 Phase B + CR-018.

REQ refs:
- REQ_F_NOT_001 / REQ_F_NOT_002 — channel conformance + bundled
  adapter selection.
- REQ_NF_NOT_002 — canonical body determinism (identical payloads
  produce identical Block Kit JSON).
- REQ_NF_NOT_003 / REQ_SDD_NOT_007 — credential safety: webhook
  URL is read lazily from env-var; never appears in logs or in
  the channel's repr.
"""

from __future__ import annotations

import json
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
from trading_system.notifications.channels.slack import (
    DEFAULT_WEBHOOK_URL_ENV,
    SlackNotificationChannel,
    render_block_kit,
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


# ---------------------------------------------------------------------------
# Construction invariants
# ---------------------------------------------------------------------------


def test_default_webhook_env_var_is_documented_name() -> None:
    """CR-018 documents `TRADING_BOT_SLACK_WEBHOOK_URL`. Deployment
    recipes rely on this exact name."""
    assert DEFAULT_WEBHOOK_URL_ENV == "TRADING_BOT_SLACK_WEBHOOK_URL"


def test_constructor_rejects_empty_env_name() -> None:
    with pytest.raises(ValueError, match="webhook_url_env"):
        SlackNotificationChannel(webhook_url_env="")


def test_constructor_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        SlackNotificationChannel(timeout_seconds=0)


# ---------------------------------------------------------------------------
# Credential safety — REQ_NF_NOT_003 / REQ_SDD_NOT_007
# ---------------------------------------------------------------------------


def test_repr_does_not_leak_webhook_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TRADING_BOT_SLACK_WEBHOOK_URL",
        "https://hooks.slack.com/services/SECRET/SECRET/SECRET",
    )
    channel = SlackNotificationChannel()
    rendered = repr(channel)
    assert "SECRET" not in rendered
    assert "https://hooks.slack.com" not in rendered


def test_deliver_returns_err_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRADING_BOT_SLACK_WEBHOOK_URL", raising=False)
    channel = SlackNotificationChannel()
    payload = _anomaly()
    match channel.deliver(payload):
        case Err(reason):
            assert reason.startswith("notifications:slack:webhook_url_env_unset:")
        case _:
            raise AssertionError("expected Err on missing env var")


# ---------------------------------------------------------------------------
# Block Kit rendering — TC_NOT_001 conformance
# ---------------------------------------------------------------------------


def test_render_kill_switch_event_carries_severity_color() -> None:
    event = KillSwitchEvent(
        snapshot_id=SnapshotId("snap-1"),
        state_from=KillSwitchState.ACTIVE,
        state_to=KillSwitchState.KILL,
        trigger_code="financial:household_drawdown:kill",
        severity="KILL",
        summary="household drawdown 0.20 >= 0.15",
    )
    body = render_block_kit(event)
    assert body["text"].startswith("trading-bot KS KILL")
    assert body["attachments"][0]["color"] == "#c0392b"  # red
    blocks = body["attachments"][0]["blocks"]
    assert blocks[0]["type"] == "header"
    assert "financial:household_drawdown:kill" in blocks[0]["text"]["text"]


def test_render_trade_approval_includes_account_quantity_loss() -> None:
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
    body = render_block_kit(approval)
    rendered = json.dumps(body)
    assert "default" in rendered
    assert "buy" in rendered.lower()
    assert "ASML.AS" in rendered
    assert "250.00" in rendered
    assert "yield>4.5" in rendered


def test_render_summary_includes_exposure_and_equity() -> None:
    summary = Summary(
        schedule="daily",
        account_id=_ACCOUNT,
        as_of=_AT,
        equity_after_tax=Money(Decimal("12345.67"), Currency.EUR),
        exposure={InstrumentClass.STOCK: Decimal("0.6")},
    )
    body = render_block_kit(summary)
    rendered = json.dumps(body)
    assert "12345.67" in rendered
    assert "daily summary" in rendered
    assert "stock" in rendered.lower() or "STOCK" in rendered


def test_render_anomaly_alert_severity_mapping() -> None:
    alert = AnomalyAlert(
        code="execution:reject",
        severity="WARN",
        account_id=_ACCOUNT,
        message="broker rejected order",
        at=_AT,
    )
    body = render_block_kit(alert)
    assert body["attachments"][0]["color"] == "#e67e22"  # orange for WARN
    assert "execution:reject" in body["text"]


def test_render_error_uses_grey_color() -> None:
    err = Error(
        code="config:io:missing",
        detail="config/notifications.yaml not found",
        at=_AT,
    )
    body = render_block_kit(err)
    assert body["attachments"][0]["color"] == "#7f8c8d"
    assert "config:io:missing" in body["text"]


# ---------------------------------------------------------------------------
# REQ_NF_NOT_002 — byte-identical replay on identical inputs
# ---------------------------------------------------------------------------


def test_block_kit_serialisation_is_byte_identical_across_calls() -> None:
    payload = _anomaly()
    a = json.dumps(render_block_kit(payload), sort_keys=True)
    b = json.dumps(render_block_kit(payload), sort_keys=True)
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
