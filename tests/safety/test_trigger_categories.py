"""Kill-switch trigger-category coverage — REQ_SDD_TST_005.

REQ_SDD_TST_005 — Kill-switch tests SHALL cover every trigger
code (financial, strategy, execution, integrity); every state
transition SHALL produce a non-empty audit snapshot.

This file is the closed conformance test for the four trigger
categories. It builds a fresh ``StateManager`` for each category,
raises a KILL-severity trigger, and asserts:

  1. The state transitions to ``KILL``.
  2. A non-empty ``AuditSnapshot`` is produced.
  3. The snapshot's ``trigger.category`` matches what was raised.

Individual category-specific behaviour (drawdown detectors for
FINANCIAL, walk-forward detectors for STRATEGY, etc.) lives in
the corresponding subsystem's test suite; this file pins the
category-coverage property at the safety-layer boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.safety import (
    AlwaysValidVerifier,
    MemoryAlertChannel,
    MemorySnapshotSink,
    StateManager,
)


def _trigger(category: TriggerCategory) -> KillSwitchTrigger:
    """Build a KILL-severity trigger for ``category``."""
    return KillSwitchTrigger(
        category=category,
        code=f"{category.value}_breach",
        message=f"{category.value} trigger fired",
        severity="KILL",
        raised_at=datetime(2026, 5, 21, 12, 0, tzinfo=UTC),
        snapshot_id=SnapshotId(f"snap-{category.value}"),
    )


def _manager() -> tuple[StateManager, MemorySnapshotSink]:
    sink = MemorySnapshotSink()
    mgr = StateManager(
        verifier=AlwaysValidVerifier(),
        snapshot_sink=sink,
        alert_channels=[MemoryAlertChannel()],
    )
    return mgr, sink


@pytest.mark.parametrize(
    "category",
    list(TriggerCategory),
    ids=lambda c: c.value,
)
def test_kill_switch_transitions_on_every_trigger_category(
    category: TriggerCategory,
) -> None:
    """REQ_SDD_TST_005 — every TriggerCategory SHALL drive the
    state manager into KILL on a KILL-severity trigger, and SHALL
    produce a non-empty audit snapshot recording the trigger."""
    mgr, sink = _manager()
    assert mgr.state() is KillSwitchState.ACTIVE

    outcome = mgr.raise_trigger(_trigger(category))
    # raise_trigger returns Ok on a fresh transition (Err only on
    # repeat triggers); we don't pattern-match here, just assert
    # the side effect on state + audit sink.
    del outcome

    assert mgr.state() is KillSwitchState.KILL, (
        f"{category.value} trigger SHALL drive the manager to KILL"
    )
    audit = sink.snapshots
    assert len(audit) >= 1, (
        f"{category.value} trigger SHALL produce ≥ 1 audit snapshot"
    )
    # The most recent snapshot carries the trigger's code; the
    # category isn't stored on AuditSnapshot directly (the
    # snapshot is self-contained per REQ_NF_AUD_001 — no need to
    # re-import the TriggerCategory enum to interpret it). Match
    # by code prefix instead.
    latest = audit[-1]
    assert latest.trigger_code.startswith(category.value), (
        f"snapshot trigger_code {latest.trigger_code!r} "
        f"does not match category {category.value!r}"
    )
    assert latest.severity == "KILL"
    assert latest.state_to is KillSwitchState.KILL


def test_every_trigger_category_has_a_test() -> None:
    """REQ_SDD_TST_005 closed-set guard — the parametrized test
    above SHALL cover every TriggerCategory the enum declares.
    If a new category is added without this test re-parametrizing,
    the count assertion below fails."""
    expected = {c.value for c in TriggerCategory}
    assert expected == {"financial", "strategy", "execution", "integrity"}, (
        f"TriggerCategory enum changed shape — got {expected}; "
        "update REQ_SDD_TST_005 coverage in test_trigger_categories.py"
    )
