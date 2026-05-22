"""Kill-switch trip / recovery drill — Phase 6 operational test.

End-to-end scenario walking the full kill-switch lifecycle:

  ACTIVE → DEGRADED → KILL → recovery (rejected, then accepted) → ACTIVE

The drill exercises every ``TriggerCategory`` along the way
(REQ_S_KS_003..006), confirms the state manager produces audit
snapshots for every transition (REQ_NF_AUD_001), verifies that
``must_halt()`` flips to True at KILL and back to False after
recovery (REQ_S_KS_011 + REQ_S_KS_007), and asserts the
``NotificationFanOut`` bridge sees one ``KillSwitchEvent``
per state transition (REQ_F_NOT_003 / REQ_SDD_NOT_002).

Unlike the unit-level tests in ``tests/safety/`` and the BDD
scenarios in ``tests/bdd/``, this drill builds the full stack
(StateManager + MemorySnapshotSink + MemoryAlertChannel +
NotificationFanOut + MemoryNotificationChannel) and walks one
contiguous scenario from boot to recovery, asserting at each
checkpoint. Operators run this as the final pre-deployment
confidence check.

REQ refs (drill-coverage):
- REQ_S_KS_001 — three-state machine (ACTIVE / DEGRADED / KILL).
- REQ_S_KS_002 — non-bypassable: only ``raise_trigger`` mutates
  state; ``must_halt()`` is the single decision boundary.
- REQ_S_KS_003 — financial triggers (drawdown).
- REQ_S_KS_004 — strategy-instability triggers (walk-forward
  collapse).
- REQ_S_KS_005 — execution-anomaly triggers (broker rejection).
- REQ_S_KS_006 — system-integrity triggers (registry corruption).
- REQ_S_KS_007 — operator-confirmed recovery.
- REQ_S_KS_008 — recovery is the only path back to ACTIVE.
- REQ_S_KS_009 — recovery conditions: drawdown + integrity +
  backtests stable + manual confirmation.
- REQ_S_KS_010 — kill-switch configuration immutable at runtime.
- REQ_S_KS_011 — every BrokerAdapter submission preceded by
  ``must_halt()`` check.
- REQ_NF_AUD_001 — non-empty audit snapshot per transition.
- REQ_F_NOT_003 / REQ_SDD_NOT_002 — KillSwitchEvent dispatch
  through ``NotificationFanOut`` on every transition.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.notifications import (
    KillSwitchEvent,
    MemoryNotificationChannel,
    NotificationFanOut,
    RetryPolicy,
)
from trading_system.result import Err, Ok
from trading_system.safety import (
    AlwaysInvalidVerifier,
    AlwaysValidVerifier,
    MemoryAlertChannel,
    MemorySnapshotSink,
    RecoveryConditions,
    StateManager,
)


# Wall-clock-free clock for the drill — every step advances by a
# documented increment so the audit trail is deterministic.
_T0 = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _trigger(
    category: TriggerCategory,
    severity: str,
    code: str,
    *,
    at_seconds: int,
) -> KillSwitchTrigger:
    return KillSwitchTrigger(
        category=category,
        code=code,
        message=f"{category.value} {severity.lower()}: {code}",
        severity=severity,  # type: ignore[arg-type]
        raised_at=_at(at_seconds),
        snapshot_id=SnapshotId(f"snap-{code}"),
    )


@pytest.fixture
def drill():  # type: ignore[no-untyped-def]
    """Build the full stack — state manager + audit sink + alert
    channel + notification fanout + memory notification channel —
    and return a small bag the test methods walk through."""
    sink = MemorySnapshotSink()
    alerts = MemoryAlertChannel()
    notif_channel = MemoryNotificationChannel()
    # Zero-delay retry policy — the drill doesn't need real backoff.
    fanout = NotificationFanOut(
        channels=(notif_channel,),
        retry_policy=RetryPolicy(
            max_attempts=1, base_delay_seconds=0.0, growth_factor=1.0
        ),
        sleep=lambda _seconds: None,
    )
    mgr = StateManager(
        verifier=AlwaysValidVerifier(),
        snapshot_sink=sink,
        alert_channels=[alerts],
        notification_fanout=fanout,
    )
    assert mgr.state() is KillSwitchState.ACTIVE
    assert mgr.must_halt() is False
    return {
        "mgr": mgr,
        "sink": sink,
        "alerts": alerts,
        "notif_channel": notif_channel,
        "fanout": fanout,
    }


# ---------------------------------------------------------------------------
# Scenario 1 — boot-clean
# ---------------------------------------------------------------------------


def test_drill_boots_clean(drill) -> None:  # type: ignore[no-untyped-def]
    """REQ_S_KS_001 — at boot the state SHALL be ACTIVE, the
    halt boolean SHALL be False, and the audit log SHALL be
    empty (no transition has occurred yet)."""
    mgr = drill["mgr"]
    sink = drill["sink"]
    notif_channel = drill["notif_channel"]
    assert mgr.state() is KillSwitchState.ACTIVE
    assert mgr.must_halt() is False
    assert sink.snapshots == []
    assert notif_channel.delivered == []


# ---------------------------------------------------------------------------
# Scenario 2 — DEGRADE financial trigger
# ---------------------------------------------------------------------------


def test_drill_degrade_financial_trigger_moves_to_degraded(drill) -> None:  # type: ignore[no-untyped-def]
    """REQ_S_KS_003 — financial-trigger family. A DEGRADE-severity
    drawdown breach SHALL move the state to DEGRADED but SHALL NOT
    halt trading; the audit log records one entry and the
    notification fanout dispatches one KillSwitchEvent."""
    mgr = drill["mgr"]
    sink = drill["sink"]
    notif_channel = drill["notif_channel"]

    mgr.raise_trigger(
        _trigger(
            TriggerCategory.FINANCIAL,
            "DEGRADE",
            "drawdown_warning",
            at_seconds=10,
        )
    )

    assert mgr.state() is KillSwitchState.DEGRADED
    assert mgr.must_halt() is False, (
        "REQ_S_KS_011 — DEGRADED state SHALL NOT block trading "
        "(only KILL does); operator may continue with caution"
    )
    assert len(sink.snapshots) == 1
    snap = sink.snapshots[0]
    assert snap.state_from is KillSwitchState.ACTIVE
    assert snap.state_to is KillSwitchState.DEGRADED
    assert snap.trigger_code == "drawdown_warning"
    assert snap.severity == "DEGRADE"

    assert len(notif_channel.delivered) == 1
    event = notif_channel.delivered[0]
    assert isinstance(event, KillSwitchEvent)
    assert event.state_from is KillSwitchState.ACTIVE
    assert event.state_to is KillSwitchState.DEGRADED
    assert event.severity == "DEGRADE"


# ---------------------------------------------------------------------------
# Scenario 3 — STRATEGY instability under DEGRADE
# ---------------------------------------------------------------------------


def test_drill_strategy_trigger_stays_degraded(drill) -> None:  # type: ignore[no-untyped-def]
    """REQ_S_KS_004 — strategy-instability triggers (walk-forward
    collapse) feed the state machine. Already-DEGRADED state plus
    another DEGRADE SHALL remain DEGRADED but record an audit row
    (same-state DEGRADE is idempotent on state, additive on log)."""
    mgr = drill["mgr"]
    sink = drill["sink"]
    notif_channel = drill["notif_channel"]

    # Move to DEGRADED first.
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.FINANCIAL,
            "DEGRADE",
            "drawdown_warning",
            at_seconds=10,
        )
    )
    assert mgr.state() is KillSwitchState.DEGRADED

    # Add a strategy DEGRADE — state stays at DEGRADED.
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.STRATEGY,
            "DEGRADE",
            "walk_forward_collapse",
            at_seconds=20,
        )
    )
    assert mgr.state() is KillSwitchState.DEGRADED
    # The strategy event was logged.
    assert len(sink.snapshots) == 2
    assert sink.snapshots[-1].trigger_code == "walk_forward_collapse"
    assert sink.snapshots[-1].state_from is KillSwitchState.DEGRADED
    assert sink.snapshots[-1].state_to is KillSwitchState.DEGRADED
    assert len(notif_channel.delivered) == 2


# ---------------------------------------------------------------------------
# Scenario 4 — KILL execution-anomaly trigger
# ---------------------------------------------------------------------------


def test_drill_execution_kill_trigger_halts_trading(drill) -> None:  # type: ignore[no-untyped-def]
    """REQ_S_KS_005 — execution-anomaly triggers (broker
    rejection spike, slippage burst). A KILL-severity trigger
    SHALL move the state to KILL and ``must_halt()`` SHALL
    return True so the next ``BrokerAdapter.submit`` is
    rejected (REQ_S_KS_011)."""
    mgr = drill["mgr"]
    sink = drill["sink"]
    notif_channel = drill["notif_channel"]

    mgr.raise_trigger(
        _trigger(
            TriggerCategory.EXECUTION,
            "KILL",
            "broker_rejection_spike",
            at_seconds=30,
        )
    )

    assert mgr.state() is KillSwitchState.KILL
    assert mgr.must_halt() is True, (
        "REQ_S_KS_011 — must_halt SHALL be True under KILL; "
        "the BrokerAdapter.submit call site is blocked"
    )
    assert len(sink.snapshots) == 1
    snap = sink.snapshots[0]
    assert snap.state_from is KillSwitchState.ACTIVE
    assert snap.state_to is KillSwitchState.KILL
    assert snap.severity == "KILL"
    assert len(notif_channel.delivered) == 1
    assert notif_channel.delivered[0].severity == "KILL"


# ---------------------------------------------------------------------------
# Scenario 5 — INTEGRITY trigger on already-KILL state is idempotent
# ---------------------------------------------------------------------------


def test_drill_integrity_kill_on_killed_state_is_idempotent(drill) -> None:  # type: ignore[no-untyped-def]
    """REQ_S_KS_006 — system-integrity triggers (registry
    corruption). Firing a KILL on an already-KILL state SHALL
    NOT regress to ACTIVE; the state stays KILL and an audit
    row is appended."""
    mgr = drill["mgr"]
    sink = drill["sink"]

    # Move to KILL first.
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.EXECUTION,
            "KILL",
            "broker_rejection_spike",
            at_seconds=30,
        )
    )
    assert mgr.state() is KillSwitchState.KILL

    # Fire integrity KILL on top.
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.INTEGRITY,
            "KILL",
            "registry_corruption",
            at_seconds=40,
        )
    )
    assert mgr.state() is KillSwitchState.KILL
    assert mgr.must_halt() is True
    # Two audit rows; the second is the idempotent-on-state KILL.
    assert len(sink.snapshots) == 2
    assert sink.snapshots[-1].state_from is KillSwitchState.KILL
    assert sink.snapshots[-1].state_to is KillSwitchState.KILL
    assert sink.snapshots[-1].trigger_code == "registry_corruption"


# ---------------------------------------------------------------------------
# Scenario 6 — recovery REJECTED when conditions are unmet
# ---------------------------------------------------------------------------


def test_drill_recovery_rejected_when_drawdown_not_recovered(drill) -> None:  # type: ignore[no-untyped-def]
    """REQ_S_KS_009 — every recovery condition SHALL clear before
    recovery is granted. A request with drawdown_recovered=False
    SHALL return Err and the state SHALL remain KILL."""
    mgr = drill["mgr"]

    # Move to KILL.
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.EXECUTION,
            "KILL",
            "broker_rejection_spike",
            at_seconds=30,
        )
    )

    conditions = RecoveryConditions(
        drawdown_recovered=False,
        integrity_restored=True,
        backtests_stable=True,
    )
    outcome = mgr.request_recovery(
        "valid-token", conditions, at=_at(60)
    )
    assert isinstance(outcome, Err)
    assert outcome.error == "safety:recovery_conditions_unmet"
    assert mgr.state() is KillSwitchState.KILL
    assert mgr.must_halt() is True


# ---------------------------------------------------------------------------
# Scenario 7 — recovery REJECTED with invalid operator token
# ---------------------------------------------------------------------------


def test_drill_recovery_rejected_with_invalid_token() -> None:
    """REQ_S_KS_009 — manual operator confirmation (HMAC token)
    SHALL be required. A KILL state with all conditions met BUT
    a bad token SHALL return Err."""
    # Build a fresh stack with the AlwaysInvalidVerifier — every
    # token is rejected.
    sink = MemorySnapshotSink()
    alerts = MemoryAlertChannel()
    mgr = StateManager(
        verifier=AlwaysInvalidVerifier(),
        snapshot_sink=sink,
        alert_channels=[alerts],
    )
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.EXECUTION,
            "KILL",
            "broker_rejection_spike",
            at_seconds=30,
        )
    )
    conditions = RecoveryConditions(
        drawdown_recovered=True,
        integrity_restored=True,
        backtests_stable=True,
    )
    outcome = mgr.request_recovery("bad-token", conditions, at=_at(60))
    assert isinstance(outcome, Err)
    assert outcome.error == "safety:invalid_operator_token"
    assert mgr.state() is KillSwitchState.KILL


# ---------------------------------------------------------------------------
# Scenario 8 — successful recovery returns to ACTIVE
# ---------------------------------------------------------------------------


def test_drill_recovery_succeeds_with_all_conditions_met(drill) -> None:  # type: ignore[no-untyped-def]
    """REQ_S_KS_007 + REQ_S_KS_008 — successful recovery is the
    only path back to ACTIVE. Conditions met + valid token →
    state moves to ACTIVE; must_halt returns False again."""
    mgr = drill["mgr"]
    sink = drill["sink"]
    notif_channel = drill["notif_channel"]

    # Trip to KILL.
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.EXECUTION,
            "KILL",
            "broker_rejection_spike",
            at_seconds=30,
        )
    )
    assert mgr.state() is KillSwitchState.KILL
    initial_snapshot_count = len(sink.snapshots)
    initial_notif_count = len(notif_channel.delivered)

    # Recover.
    conditions = RecoveryConditions(
        drawdown_recovered=True,
        integrity_restored=True,
        backtests_stable=True,
    )
    outcome = mgr.request_recovery(
        "valid-token", conditions, at=_at(60)
    )
    assert isinstance(outcome, Ok)

    # Final state.
    assert mgr.state() is KillSwitchState.ACTIVE
    assert mgr.must_halt() is False

    # One additional audit snapshot recording the recovery.
    assert len(sink.snapshots) == initial_snapshot_count + 1
    recovery_snap = sink.snapshots[-1]
    assert recovery_snap.state_from is KillSwitchState.KILL
    assert recovery_snap.state_to is KillSwitchState.ACTIVE
    assert recovery_snap.severity == "RECOVERY"
    assert recovery_snap.trigger_code == "manual_recovery"

    # One additional notification event for the recovery.
    assert len(notif_channel.delivered) == initial_notif_count + 1
    recovery_event = notif_channel.delivered[-1]
    assert recovery_event.severity == "RECOVERY"
    assert recovery_event.state_to is KillSwitchState.ACTIVE


# ---------------------------------------------------------------------------
# Scenario 9 — post-recovery trip works again
# ---------------------------------------------------------------------------


def test_drill_post_recovery_can_re_trip(drill) -> None:  # type: ignore[no-untyped-def]
    """REQ_S_KS_001 family — after recovery, the kill switch
    SHALL accept a new trigger and move back to KILL. Recovery
    is reversible by design; the operator can be wrong twice."""
    mgr = drill["mgr"]

    # Trip → recover → trip again.
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.EXECUTION,
            "KILL",
            "broker_rejection_spike",
            at_seconds=30,
        )
    )
    conditions = RecoveryConditions(
        drawdown_recovered=True,
        integrity_restored=True,
        backtests_stable=True,
    )
    mgr.request_recovery("valid-token", conditions, at=_at(60))
    assert mgr.state() is KillSwitchState.ACTIVE

    # Second trip.
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.INTEGRITY,
            "KILL",
            "registry_second_breach",
            at_seconds=120,
        )
    )
    assert mgr.state() is KillSwitchState.KILL
    assert mgr.must_halt() is True


# ---------------------------------------------------------------------------
# Scenario 10 — full lifecycle drill in a single linear walk
# ---------------------------------------------------------------------------


def test_drill_full_lifecycle(drill) -> None:  # type: ignore[no-untyped-def]
    """REQ_S_KS_001..011 — single contiguous drill covering
    every trigger family + recovery in one walk:

    1. Boot clean — ACTIVE, must_halt=False.
    2. Financial DEGRADE — DEGRADED, must_halt=False.
    3. Strategy DEGRADE — still DEGRADED, audit row appended.
    4. Execution KILL — KILL, must_halt=True.
    5. Recovery (rejected — bad conditions).
    6. Recovery (accepted — all conditions met) — ACTIVE,
       must_halt=False.
    7. Integrity KILL — KILL again, must_halt=True.
    8. Recovery — ACTIVE.

    Verifies the count of audit snapshots + dispatched events
    matches the expected transition count, and the state
    timeline is exactly the documented sequence."""
    mgr = drill["mgr"]
    sink = drill["sink"]
    notif_channel = drill["notif_channel"]

    timeline: list[KillSwitchState] = [mgr.state()]

    # Step 1 — financial DEGRADE.
    mgr.raise_trigger(
        _trigger(TriggerCategory.FINANCIAL, "DEGRADE", "drawdown", at_seconds=10)
    )
    timeline.append(mgr.state())

    # Step 2 — strategy DEGRADE (no upgrade).
    mgr.raise_trigger(
        _trigger(TriggerCategory.STRATEGY, "DEGRADE", "wf_collapse", at_seconds=20)
    )
    timeline.append(mgr.state())

    # Step 3 — execution KILL.
    mgr.raise_trigger(
        _trigger(TriggerCategory.EXECUTION, "KILL", "broker_spike", at_seconds=30)
    )
    timeline.append(mgr.state())

    # Step 4 — recovery rejected (drawdown not recovered).
    bad_conds = RecoveryConditions(
        drawdown_recovered=False,
        integrity_restored=True,
        backtests_stable=True,
    )
    assert isinstance(
        mgr.request_recovery("valid", bad_conds, at=_at(40)), Err
    )
    timeline.append(mgr.state())

    # Step 5 — recovery accepted.
    good_conds = RecoveryConditions(
        drawdown_recovered=True,
        integrity_restored=True,
        backtests_stable=True,
    )
    assert isinstance(
        mgr.request_recovery("valid", good_conds, at=_at(50)), Ok
    )
    timeline.append(mgr.state())

    # Step 6 — integrity KILL after recovery.
    mgr.raise_trigger(
        _trigger(
            TriggerCategory.INTEGRITY, "KILL", "registry_corruption", at_seconds=60
        )
    )
    timeline.append(mgr.state())

    # Step 7 — final recovery.
    assert isinstance(
        mgr.request_recovery("valid", good_conds, at=_at(70)), Ok
    )
    timeline.append(mgr.state())

    # The full state timeline:
    expected_timeline = [
        KillSwitchState.ACTIVE,    # boot
        KillSwitchState.DEGRADED,  # financial DEGRADE
        KillSwitchState.DEGRADED,  # strategy DEGRADE (idempotent on state)
        KillSwitchState.KILL,      # execution KILL
        KillSwitchState.KILL,      # bad recovery — state unchanged
        KillSwitchState.ACTIVE,    # recovery success
        KillSwitchState.KILL,      # integrity KILL
        KillSwitchState.ACTIVE,    # final recovery
    ]
    assert timeline == expected_timeline

    # Audit snapshot count walk-through:
    #   1. financial DEGRADE (ACTIVE → DEGRADED)
    #   2. strategy DEGRADE (DEGRADED → DEGRADED idempotent — still
    #      records via _record_event in state_manager.py:116)
    #   3. execution KILL (DEGRADED → KILL)
    #   4. recovery rejected — NO snapshot (didn't transition)
    #   5. recovery accepted (KILL → ACTIVE)
    #   6. integrity KILL (ACTIVE → KILL)
    #   7. final recovery (KILL → ACTIVE)
    # ⇒ 6 audit rows.
    assert len(sink.snapshots) == 6

    # Notification dispatch count matches snapshot count — every
    # snapshot fires a KillSwitchEvent.
    assert len(notif_channel.delivered) == 6

    # Final state is ACTIVE; must_halt is False.
    assert mgr.state() is KillSwitchState.ACTIVE
    assert mgr.must_halt() is False
