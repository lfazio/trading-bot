"""Tests for ``NotificationFanOut`` + ``RetryPolicy``
(REQ_F_NOT_008, REQ_SDS_NOT_004, REQ_SDD_NOT_004, REQ_SDD_ERR_005)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import KillSwitchState
from trading_system.notifications.channels.local_log import (
    MemoryNotificationChannel,
)
from trading_system.notifications.fanout import (
    NotificationFanOut,
    RetryPolicy,
)
from trading_system.notifications.payloads import (
    KillSwitchEvent,
    NotificationPayload,
)
from trading_system.result import Err, Ok, Result


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _ks_event() -> KillSwitchEvent:
    return KillSwitchEvent(
        snapshot_id=SnapshotId("snap-1"),
        state_from=KillSwitchState.ACTIVE,
        state_to=KillSwitchState.DEGRADED,
        trigger_code="financial:single_day_loss",
        severity="DEGRADE",
        summary="single-day loss breach",
    )


@dataclass(slots=True)
class _FlakyChannel:
    """Fails the first N attempts, then succeeds."""

    fail_first: int
    delivered: list[NotificationPayload] = field(default_factory=list)
    _attempts: int = 0

    def deliver(self, payload: NotificationPayload) -> Result[None, str]:
        self._attempts += 1
        if self._attempts <= self.fail_first:
            return Err(f"network:flake_{self._attempts}")
        self.delivered.append(payload)
        return Ok(None)


@dataclass(slots=True)
class _AlwaysFail:
    delivered: list[NotificationPayload] = field(default_factory=list)

    def deliver(self, payload: NotificationPayload) -> Result[None, str]:
        return Err("network:permanent")


# ---------------------------------------------------------------------------
# RetryPolicy invariants
# ---------------------------------------------------------------------------


def test_retry_policy_defaults() -> None:
    p = RetryPolicy()
    assert p.max_attempts == 3
    assert p.delay_for(0) == 0.0
    assert p.delay_for(1) > 0
    assert p.delay_for(2) > p.delay_for(1)


def test_retry_policy_rejects_zero_max_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)


def test_retry_policy_rejects_negative_base_delay() -> None:
    with pytest.raises(ValueError, match="base_delay_seconds"):
        RetryPolicy(base_delay_seconds=-1.0)


def test_retry_policy_rejects_growth_factor_below_1() -> None:
    with pytest.raises(ValueError, match="growth_factor"):
        RetryPolicy(growth_factor=0.5)


# ---------------------------------------------------------------------------
# NotificationFanOut behaviour
# ---------------------------------------------------------------------------


def test_dispatch_delivers_to_every_channel() -> None:
    a = MemoryNotificationChannel()
    b = MemoryNotificationChannel()
    sleeps: list[float] = []
    fan = NotificationFanOut(
        channels=(a, b),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
        sleep=sleeps.append,
    )
    fan.dispatch(_ks_event())
    assert len(a.delivered) == 1
    assert len(b.delivered) == 1


def test_dispatch_retries_until_success() -> None:
    flaky = _FlakyChannel(fail_first=2)
    sleeps: list[float] = []
    fan = NotificationFanOut(
        channels=(flaky,),
        retry_policy=RetryPolicy(
            max_attempts=3, base_delay_seconds=0.01
        ),
        sleep=sleeps.append,
    )
    fan.dispatch(_ks_event())
    # 2 failed attempts + 1 successful = 3 total.
    assert len(flaky.delivered) == 1
    # ``delay_for(0)`` returns 0 — the fan-out skips sleep on the
    # first attempt — so the first sleep we observe is the retry
    # delay for attempt 1 (> 0). The second observed sleep is the
    # retry delay for attempt 2 (still > 0; exponentially larger).
    assert len(sleeps) == 2  # one sleep per retry
    assert sleeps[0] > 0
    assert sleeps[1] > sleeps[0]


def test_dispatch_gives_up_after_max_attempts() -> None:
    bad = _AlwaysFail()
    sleeps: list[float] = []
    fan = NotificationFanOut(
        channels=(bad,),
        retry_policy=RetryPolicy(
            max_attempts=2, base_delay_seconds=0.01
        ),
        sleep=sleeps.append,
    )
    # Should NOT raise — failures are logged structured.
    fan.dispatch(_ks_event())
    assert bad.delivered == []


def test_dispatch_channel_failure_does_not_block_siblings() -> None:
    """REQ_NF_NOT_001 — one channel's permanent failure SHALL NOT
    affect the others' delivery."""
    bad = _AlwaysFail()
    good = MemoryNotificationChannel()
    fan = NotificationFanOut(
        channels=(bad, good),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
        sleep=lambda _t: None,
    )
    fan.dispatch(_ks_event())
    assert len(good.delivered) == 1


def test_dispatch_observation_order_sorted_by_class_name() -> None:
    """REQ_SDD_NOT_004 — sorted observation for deterministic logs.

    The MemoryNotificationChannel records the SAME payload on each
    call; we use two named subclasses so the sort order is visible
    in the side effects."""

    @dataclass(slots=True)
    class _ZChannel:
        log: list[str] = field(default_factory=list)

        def deliver(self, payload: NotificationPayload) -> Result[None, str]:
            self.log.append("Z")
            return Ok(None)

    @dataclass(slots=True)
    class _AChannel:
        log: list[str]

        def deliver(self, payload: NotificationPayload) -> Result[None, str]:
            self.log.append("A")
            return Ok(None)

    shared: list[str] = []
    z = _ZChannel(log=shared)
    a = _AChannel(log=shared)
    # Insert in non-alphabetical order.
    fan = NotificationFanOut(
        channels=(z, a),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
        sleep=lambda _t: None,
    )
    fan.dispatch(_ks_event())
    # _AChannel (starts with A) SHALL fire first.
    assert shared == ["A", "Z"]


def test_dispatch_replay_determinism() -> None:
    """Identical fan-out state + payload across two dispatch calls
    SHALL produce identical observations (REQ_NF_NOT_002 family)."""
    a = MemoryNotificationChannel()
    b = MemoryNotificationChannel()
    fan = NotificationFanOut(
        channels=(a, b),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
        sleep=lambda _t: None,
    )
    fan.dispatch(_ks_event())
    fan.dispatch(_ks_event())
    assert a.delivered == b.delivered
    assert len(a.delivered) == 2
