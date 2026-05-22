"""Performance gate — REQ_TP_GAT_001.

REQ_TP_GAT_001 — ``must_halt()`` SHALL execute in under 1 µs.
Hot-path discipline: the kill-switch check sits in front of every
broker submission (REQ_SDS_ARC_003), so a slow ``must_halt()``
slows the entire trading loop.

The benchmark is opt-in via ``@pytest.mark.perf`` so the default
``pytest -q`` run stays fast. CI runs ``pytest -m perf`` separately
and asserts the budget; the test fails when wall-clock per call
exceeds the 1 µs ceiling by more than ~5× (giving headroom for
slower CI runners while still catching genuine regressions).

The ``must_halt`` body is ``return self._state is KillSwitchState.KILL``
— a single attribute access + identity compare. On any modern CPU
this is in the sub-100 ns range; the 1 µs target is comfortable.
"""

from __future__ import annotations

import pytest

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import (
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.safety import (
    AlwaysValidVerifier,
    MemoryAlertChannel,
    MemorySnapshotSink,
    StateManager,
)
from datetime import UTC, datetime


pytestmark = pytest.mark.perf


_MUST_HALT_BUDGET_SECONDS = 1e-6  # 1 µs hard ceiling per REQ_TP_GAT_001
_HEADROOM = 5.0  # accept up to 5× the ceiling so slow CI runners don't flap


def _manager_active() -> StateManager:
    return StateManager(
        verifier=AlwaysValidVerifier(),
        snapshot_sink=MemorySnapshotSink(),
        alert_channels=[MemoryAlertChannel()],
    )


def _manager_killed() -> StateManager:
    mgr = _manager_active()
    mgr.raise_trigger(
        KillSwitchTrigger(
            category=TriggerCategory.FINANCIAL,
            code="bench_setup",
            message="setup trip for benchmark",
            severity="KILL",
            raised_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
            snapshot_id=SnapshotId("bench-snap"),
        )
    )
    return mgr


def test_must_halt_active_under_1us(benchmark) -> None:  # type: ignore[no-untyped-def]
    """REQ_TP_GAT_001 — must_halt() on an ACTIVE state SHALL
    complete in < 1 µs (the non-tripped hot path). 5× headroom
    on slow CI runners; flag a regression on >5 µs."""
    mgr = _manager_active()
    benchmark(mgr.must_halt)
    mean = benchmark.stats.stats.mean
    assert mean < _MUST_HALT_BUDGET_SECONDS * _HEADROOM, (
        f"must_halt() mean {mean * 1e6:.3f} µs exceeds "
        f"{_MUST_HALT_BUDGET_SECONDS * _HEADROOM * 1e6:.0f} µs"
    )


def test_must_halt_killed_under_1us(benchmark) -> None:  # type: ignore[no-untyped-def]
    """REQ_TP_GAT_001 — must_halt() on a KILL state SHALL also
    complete in < 1 µs (same hot path; the branch direction
    differs but the work is identical)."""
    mgr = _manager_killed()
    assert mgr.must_halt() is True
    benchmark(mgr.must_halt)
    mean = benchmark.stats.stats.mean
    assert mean < _MUST_HALT_BUDGET_SECONDS * _HEADROOM
