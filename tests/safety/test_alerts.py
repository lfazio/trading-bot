"""Tests for ``trading_system.safety.alerts``."""

from __future__ import annotations

import pytest

from trading_system.result import Err, Ok
from trading_system.safety.alerts import (
    AlertChannel,
    FlakyAlertChannel,
    MemoryAlertChannel,
    deliver_with_retry,
)


class TestProtocolConformance:
    def test_memory_satisfies(self) -> None:
        assert isinstance(MemoryAlertChannel(), AlertChannel)

    def test_flaky_satisfies(self) -> None:
        assert isinstance(FlakyAlertChannel(fail_first=0), AlertChannel)


class TestMemoryAlertChannel:
    def test_records_each_call(self) -> None:
        ch = MemoryAlertChannel()
        match ch.deliver("KILL", {"k": 1}):
            case Ok(_):
                pass
            case Err(reason):
                pytest.fail(f"unexpected Err: {reason}")
        assert ch.delivered == [("KILL", {"k": 1})]


class TestDeliverWithRetry:
    def test_succeeds_on_first_attempt(self) -> None:
        ch = MemoryAlertChannel()
        sleeps: list[float] = []
        result = deliver_with_retry(ch, "KILL", {"x": 1}, sleep=lambda s: sleeps.append(s))
        assert result == Ok(None)
        assert ch.delivered == [("KILL", {"x": 1})]
        assert sleeps == []  # no retries needed

    def test_retries_on_transient_failure(self) -> None:
        ch = FlakyAlertChannel(fail_first=2)
        sleeps: list[float] = []
        result = deliver_with_retry(ch, "KILL", {"x": 1}, sleep=lambda s: sleeps.append(s))
        assert result == Ok(None)
        # 3 attempts total (2 fail + 1 succeed) -> 2 sleep calls.
        assert len(sleeps) == 2
        # Exponential backoff: second sleep is twice the first.
        assert sleeps[1] == sleeps[0] * 2

    def test_exhausts_attempts_returns_err(self) -> None:
        ch = FlakyAlertChannel(fail_first=10)  # always fails
        result = deliver_with_retry(ch, "KILL", {"x": 1}, sleep=lambda s: None, max_attempts=3)
        match result:
            case Err(reason):
                assert "network:retry_pending" in reason
            case Ok(_):
                pytest.fail("expected Err after exhausting retries")

    def test_max_attempts_must_be_positive(self) -> None:
        ch = MemoryAlertChannel()
        with pytest.raises(ValueError, match="max_attempts"):
            deliver_with_retry(ch, "KILL", {}, max_attempts=0)

    def test_negative_backoff_rejected(self) -> None:
        ch = MemoryAlertChannel()
        with pytest.raises(ValueError, match="initial_backoff"):
            deliver_with_retry(ch, "KILL", {}, initial_backoff_seconds=-1.0)

    def test_zero_backoff_no_sleep(self) -> None:
        ch = FlakyAlertChannel(fail_first=1)
        sleeps: list[float] = []
        result = deliver_with_retry(
            ch,
            "KILL",
            {"x": 1},
            sleep=lambda s: sleeps.append(s),
            initial_backoff_seconds=0,
        )
        assert result == Ok(None)
        # No sleeps when backoff is zero.
        assert sleeps == []
