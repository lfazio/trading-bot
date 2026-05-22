"""BDD step definitions for ``features/kill_switch.feature``.

REQ_TP_STR_003 — kill-switch and recovery scenarios SHALL be
specified as Given/When/Then BDD scenarios so operator runbooks
stay consistent with executable tests. Step definitions below
drive a real ``StateManager`` so the scenarios verify the actual
runtime behaviour, not a mock.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pytest_bdd import given, parsers, scenarios, then, when

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.result import Err
from trading_system.safety import (
    AlwaysValidVerifier,
    MemoryAlertChannel,
    MemorySnapshotSink,
    RecoveryConditions,
    StateManager,
)


scenarios("features/kill_switch.feature")


def _build_manager() -> tuple[StateManager, MemorySnapshotSink]:
    sink = MemorySnapshotSink()
    mgr = StateManager(
        verifier=AlwaysValidVerifier(),
        snapshot_sink=sink,
        alert_channels=[MemoryAlertChannel()],
    )
    return mgr, sink


def _trigger(category: TriggerCategory, severity: str) -> KillSwitchTrigger:
    return KillSwitchTrigger(
        category=category,
        code=f"{category.value}_breach",
        message=f"{category.value} {severity} trigger",
        severity=severity,  # type: ignore[arg-type]
        raised_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        snapshot_id=SnapshotId(f"snap-{category.value}-{severity.lower()}"),
    )


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given("an ACTIVE kill switch", target_fixture="ctx")
def given_active_ks() -> dict[str, object]:
    mgr, sink = _build_manager()
    assert mgr.state() is KillSwitchState.ACTIVE
    return {"mgr": mgr, "sink": sink, "recovery_outcome": None}


@given("a KILL kill switch", target_fixture="ctx")
def given_killed_ks() -> dict[str, object]:
    mgr, sink = _build_manager()
    mgr.raise_trigger(_trigger(TriggerCategory.FINANCIAL, "KILL"))
    assert mgr.state() is KillSwitchState.KILL
    return {"mgr": mgr, "sink": sink, "recovery_outcome": None}


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("a KILL-severity financial trigger is raised")
def when_kill_financial(ctx: dict[str, object]) -> None:
    mgr: StateManager = ctx["mgr"]  # type: ignore[assignment]
    mgr.raise_trigger(_trigger(TriggerCategory.FINANCIAL, "KILL"))


@when("a DEGRADE-severity strategy trigger is raised")
def when_degrade_strategy(ctx: dict[str, object]) -> None:
    mgr: StateManager = ctx["mgr"]  # type: ignore[assignment]
    mgr.raise_trigger(_trigger(TriggerCategory.STRATEGY, "DEGRADE"))


@when("recovery is requested with all recovery conditions met")
def when_recovery_all_met(ctx: dict[str, object]) -> None:
    mgr: StateManager = ctx["mgr"]  # type: ignore[assignment]
    conditions = RecoveryConditions(
        drawdown_recovered=True,
        integrity_restored=True,
        backtests_stable=True,
    )
    ctx["recovery_outcome"] = mgr.request_recovery(
        "valid-operator-token",
        conditions,
        at=datetime(2026, 5, 22, 13, 0, tzinfo=UTC),
    )


@when("recovery is requested with at least one condition unmet")
def when_recovery_some_unmet(ctx: dict[str, object]) -> None:
    mgr: StateManager = ctx["mgr"]  # type: ignore[assignment]
    # Drawdown not recovered → recovery rejected.
    conditions = RecoveryConditions(
        drawdown_recovered=False,
        integrity_restored=True,
        backtests_stable=True,
    )  # noqa: E501 — manual_confirmation field deferred per SDD
    ctx["recovery_outcome"] = mgr.request_recovery(
        "valid-operator-token",
        conditions,
        at=datetime(2026, 5, 22, 13, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse("the kill switch state is {state}"))
def then_state_is(ctx: dict[str, object], state: str) -> None:
    mgr: StateManager = ctx["mgr"]  # type: ignore[assignment]
    expected = KillSwitchState(state.lower())
    assert mgr.state() is expected, (
        f"expected {expected}, got {mgr.state()}"
    )


@then(parsers.parse("must_halt returns {value}"))
def then_must_halt_is(ctx: dict[str, object], value: str) -> None:
    mgr: StateManager = ctx["mgr"]  # type: ignore[assignment]
    expected = value.strip() == "True"
    assert mgr.must_halt() is expected


@then("one audit snapshot is recorded")
def then_one_snapshot_recorded(ctx: dict[str, object]) -> None:
    sink: MemorySnapshotSink = ctx["sink"]  # type: ignore[assignment]
    assert len(sink.snapshots) >= 1


@then("recovery returns an Err")
def then_recovery_err(ctx: dict[str, object]) -> None:
    outcome = ctx["recovery_outcome"]
    assert isinstance(outcome, Err), (
        f"expected Err, got {outcome!r}"
    )
