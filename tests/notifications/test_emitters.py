"""``AnomalyEmitter`` + helper-function tests — CR-001 Phase B step 2.

REQ refs:
- REQ_F_NOT_007 — AnomalyAlert payloads for events short of a KS
  trip (broker rejections, strategy-lab candidate rejections).
- REQ_SDD_NOT_006 — emitters live with their upstream subsystem
  + go through NotificationFanOut.dispatch.
- REQ_NF_NOT_001 — emission is fire-and-forget through the
  fan-out; the wrapped subsystem's critical path is unblocked.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.notifications.channels.local_log import (
    MemoryNotificationChannel,
)
from trading_system.notifications.emitters import (
    AnomalyEmitter,
    emit_anomaly,
    emit_broker_rejection,
    emit_strategy_rejections,
)
from trading_system.notifications.fanout import NotificationFanOut
from trading_system.notifications.payloads import AnomalyAlert


_AT = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
_ACCOUNT = AccountId("default")


def _fanout_with_channel() -> tuple[NotificationFanOut, MemoryNotificationChannel]:
    channel = MemoryNotificationChannel()
    fanout = NotificationFanOut(channels=(channel,))
    return fanout, channel


def _now_fixed() -> datetime:
    return _AT


# ---------------------------------------------------------------------------
# AnomalyEmitter
# ---------------------------------------------------------------------------


def test_anomaly_emitter_dispatches_through_fanout() -> None:
    fanout, channel = _fanout_with_channel()
    emitter = AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT)
    emitter.emit(code="execution:reject", message="broker rejected order", now=_now_fixed)
    assert len(channel.delivered) == 1
    payload = channel.delivered[0]
    assert isinstance(payload, AnomalyAlert)
    assert payload.code == "execution:reject"
    assert payload.message == "broker rejected order"
    assert payload.account_id == _ACCOUNT
    assert payload.severity == "WARN"  # documented default
    assert payload.at == _AT


def test_anomaly_emitter_respects_explicit_severity() -> None:
    fanout, channel = _fanout_with_channel()
    emitter = AnomalyEmitter(
        fanout=fanout, account_id=_ACCOUNT, severity="URGENT"
    )
    emitter.emit(code="execution:reject", message="critical broker failure", now=_now_fixed)
    assert channel.delivered[0].severity == "URGENT"


def test_anomaly_emitter_equality_on_identical_inputs() -> None:
    """Frozen+slotted dataclass — two emitters built from the same
    fanout + account_id + severity are equal (structural equality
    is what subsystem call sites rely on). Hashability isn't
    required because NotificationFanOut holds a mutable channel
    tuple and isn't hashable; that's fine — operators construct
    one emitter at startup and reuse the reference."""
    fanout, _ = _fanout_with_channel()
    a = AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT)
    b = AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT)
    assert a == b


# ---------------------------------------------------------------------------
# emit_broker_rejection
# ---------------------------------------------------------------------------


def test_emit_broker_rejection_with_none_emitter_is_noop() -> None:
    """REQ_NF_NOT_001 / REQ_NF_ACC_001 mirror — backtest + single-
    account demos pass emitter=None and emission is a no-op."""
    emit_broker_rejection(None, reason="broker:rejected", detail="ASML.AS BUY 10")
    # No assertion needed; the documented behaviour is "no error raised".


def test_emit_broker_rejection_includes_detail() -> None:
    fanout, channel = _fanout_with_channel()
    emitter = AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT)
    emit_broker_rejection(
        emitter,
        reason="broker:no_market_data",
        detail="ASML.AS",
        now=_now_fixed,
    )
    assert len(channel.delivered) == 1
    payload = channel.delivered[0]
    assert payload.code == "broker:no_market_data"
    assert "ASML.AS" in payload.message


def test_emit_broker_rejection_without_detail() -> None:
    fanout, channel = _fanout_with_channel()
    emitter = AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT)
    emit_broker_rejection(emitter, reason="broker:currency_mismatch", now=_now_fixed)
    payload = channel.delivered[0]
    assert payload.message.startswith("broker rejected: broker:currency_mismatch")


# ---------------------------------------------------------------------------
# emit_strategy_rejections
# ---------------------------------------------------------------------------


def test_emit_strategy_rejections_dispatches_one_per_entry() -> None:
    fanout, channel = _fanout_with_channel()
    emitter = AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT)
    rejections = {
        StrategyId("alpha"): "oos_collapse",
        StrategyId("beta"): "risk_guard:dd_breach",
    }
    emit_strategy_rejections(emitter, rejections, now=_now_fixed)
    assert len(channel.delivered) == 2
    codes = [p.code for p in channel.delivered]
    assert codes == [
        "strategy_lab:oos_collapse",
        "strategy_lab:risk_guard:dd_breach",
    ]


def test_emit_strategy_rejections_sorted_by_candidate_id() -> None:
    """REQ_NF_NOT_002 family — deterministic iteration so test
    fixtures see byte-identical fan-out observation."""
    fanout, channel = _fanout_with_channel()
    emitter = AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT)
    # Insert in non-sorted order; emitter sorts before dispatch.
    rejections = [
        (StrategyId("zebra"), "registry_store_failed"),
        (StrategyId("alpha"), "oos_collapse"),
        (StrategyId("mid"), "mc:p5_drawdown_exceeds_phase_floor"),
    ]
    emit_strategy_rejections(emitter, rejections, now=_now_fixed)
    messages = [p.message for p in channel.delivered]
    # Alphabetical by candidate id (alpha < mid < zebra).
    assert "candidate alpha rejected" in messages[0]
    assert "candidate mid rejected" in messages[1]
    assert "candidate zebra rejected" in messages[2]


def test_emit_strategy_rejections_with_none_emitter_is_noop() -> None:
    emit_strategy_rejections(
        None,
        {StrategyId("alpha"): "oos_collapse"},
    )


def test_emit_strategy_rejections_empty_input_no_dispatch() -> None:
    fanout, channel = _fanout_with_channel()
    emitter = AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT)
    emit_strategy_rejections(emitter, {}, now=_now_fixed)
    assert channel.delivered == []


# ---------------------------------------------------------------------------
# generic emit_anomaly hook
# ---------------------------------------------------------------------------


def test_emit_anomaly_with_none_is_noop() -> None:
    emit_anomaly(None, code="any:code", message="any message")


def test_emit_anomaly_dispatches() -> None:
    fanout, channel = _fanout_with_channel()
    emitter = AnomalyEmitter(fanout=fanout, account_id=_ACCOUNT)
    emit_anomaly(
        emitter,
        code="risk:cross_account_concentration:ASML.AS",
        message="ASML.AS household exposure > 0.25",
        now=_now_fixed,
    )
    assert channel.delivered[0].code == (
        "risk:cross_account_concentration:ASML.AS"
    )
