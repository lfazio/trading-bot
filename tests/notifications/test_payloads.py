"""Tests for the closed ``NotificationPayload`` union shape
invariants (REQ_F_NOT_001, REQ_SDD_NOT_001)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from trading_system.notifications.payloads import (
    AnomalyAlert,
    ApprovalResponse,
    Error,
    KillSwitchEvent,
    RealizationLine,
    Summary,
    TradeApprovalRequest,
)


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# KillSwitchEvent
# ---------------------------------------------------------------------------


def _ks_event(**overrides: object) -> KillSwitchEvent:
    base = dict(
        snapshot_id=SnapshotId("snap-1"),
        state_from=KillSwitchState.ACTIVE,
        state_to=KillSwitchState.DEGRADED,
        trigger_code="financial:single_day_loss",
        severity="DEGRADE",
        summary="single-day loss breach",
    )
    base.update(overrides)
    return KillSwitchEvent(**base)  # type: ignore[arg-type]


def test_ks_event_happy_path() -> None:
    e = _ks_event()
    assert e.severity == "DEGRADE"


def test_ks_event_rejects_empty_snapshot_id() -> None:
    with pytest.raises(ValueError, match="snapshot_id"):
        _ks_event(snapshot_id=SnapshotId(""))


def test_ks_event_rejects_empty_trigger_code() -> None:
    with pytest.raises(ValueError, match="trigger_code"):
        _ks_event(trigger_code="   ")


def test_ks_event_rejects_empty_summary() -> None:
    with pytest.raises(ValueError, match="summary"):
        _ks_event(summary="")


def test_ks_event_is_frozen() -> None:
    e = _ks_event()
    with pytest.raises(Exception):
        e.severity = "KILL"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TradeApprovalRequest
# ---------------------------------------------------------------------------


def _approval_request(**overrides: object) -> TradeApprovalRequest:
    base = dict(
        request_id="req-1",
        account_id=AccountId("alpha"),
        instrument=InstrumentId("ASML.AS"),
        side=Side.BUY,
        quantity=Decimal("10"),
        expected_loss=Money(Decimal("250"), Currency.EUR),
        rationale_digest="dividend yield 5.2% > threshold 4.5%",
        requested_at=_NOW,
        expires_at=_NOW + timedelta(seconds=60),
    )
    base.update(overrides)
    return TradeApprovalRequest(**base)  # type: ignore[arg-type]


def test_approval_request_happy_path() -> None:
    r = _approval_request()
    assert r.account_id == AccountId("alpha")


def test_approval_request_rejects_empty_request_id() -> None:
    with pytest.raises(ValueError, match="request_id"):
        _approval_request(request_id="")


def test_approval_request_rejects_zero_quantity() -> None:
    with pytest.raises(ValueError, match="quantity"):
        _approval_request(quantity=Decimal("0"))


def test_approval_request_rejects_negative_quantity() -> None:
    with pytest.raises(ValueError, match="quantity"):
        _approval_request(quantity=Decimal("-1"))


def test_approval_request_rejects_empty_rationale() -> None:
    with pytest.raises(ValueError, match="rationale_digest"):
        _approval_request(rationale_digest=" ")


def test_approval_request_rejects_expires_at_le_requested_at() -> None:
    with pytest.raises(ValueError, match="expires_at"):
        _approval_request(expires_at=_NOW)


# ---------------------------------------------------------------------------
# ApprovalResponse
# ---------------------------------------------------------------------------


def test_approval_response_happy_path() -> None:
    r = ApprovalResponse(
        request_id="req-1",
        approved=True,
        operator_token="2026-05-16T12:00:00+00:00:alpha:deadbeef",
        responded_at=_NOW,
    )
    assert r.approved


def test_approval_response_rejects_empty_request_id() -> None:
    with pytest.raises(ValueError, match="request_id"):
        ApprovalResponse(
            request_id="",
            approved=False,
            operator_token="tok",
            responded_at=_NOW,
        )


def test_approval_response_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="operator_token"):
        ApprovalResponse(
            request_id="req-1",
            approved=False,
            operator_token="   ",
            responded_at=_NOW,
        )


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_summary_happy_path() -> None:
    s = Summary(
        schedule="daily",
        account_id=AccountId("alpha"),
        as_of=_NOW,
        equity_after_tax=Money(Decimal("10000"), Currency.EUR),
        exposure={InstrumentClass.STOCK: Decimal("0.7")},
    )
    assert s.schedule == "daily"


def test_summary_rejects_exposure_above_1() -> None:
    with pytest.raises(ValueError, match="exposure"):
        Summary(
            schedule="daily",
            account_id=AccountId("alpha"),
            as_of=_NOW,
            equity_after_tax=Money(Decimal("10000"), Currency.EUR),
            exposure={InstrumentClass.STOCK: Decimal("1.5")},
        )


def test_summary_rejects_non_realization_in_top_list() -> None:
    with pytest.raises(TypeError, match="RealizationLine"):
        Summary(
            schedule="daily",
            account_id=AccountId("alpha"),
            as_of=_NOW,
            equity_after_tax=Money(Decimal("10000"), Currency.EUR),
            exposure={},
            top_realizations=("not-a-line",),  # type: ignore[arg-type]
        )


def test_summary_rejects_empty_account_id() -> None:
    with pytest.raises(ValueError, match="account_id"):
        Summary(
            schedule="daily",
            account_id=AccountId(""),
            as_of=_NOW,
            equity_after_tax=Money(Decimal("10000"), Currency.EUR),
            exposure={},
        )


# ---------------------------------------------------------------------------
# AnomalyAlert + Error
# ---------------------------------------------------------------------------


def test_anomaly_alert_happy_path() -> None:
    a = AnomalyAlert(
        code="broker:rejection_rate_spike",
        severity="WARN",
        account_id=AccountId("alpha"),
        message="rejection rate 25% over 1h",
        at=_NOW,
    )
    assert a.severity == "WARN"


def test_anomaly_alert_rejects_empty_code() -> None:
    with pytest.raises(ValueError, match="code"):
        AnomalyAlert(
            code="",
            severity="INFO",
            account_id=AccountId("alpha"),
            message="msg",
            at=_NOW,
        )


def test_error_happy_path() -> None:
    e = Error(code="net:timeout", detail="WhatsApp Cloud API timeout", at=_NOW)
    assert e.code == "net:timeout"


def test_error_rejects_empty_detail() -> None:
    with pytest.raises(ValueError, match="detail"):
        Error(code="x", detail="", at=_NOW)


# ---------------------------------------------------------------------------
# RealizationLine
# ---------------------------------------------------------------------------


def test_realization_line_constructs() -> None:
    line = RealizationLine(
        instrument=InstrumentId("ASML.AS"),
        realized_after_tax=Money(Decimal("100"), Currency.EUR),
        closed_at=_NOW,
    )
    assert line.realized_after_tax.amount == Decimal("100")
