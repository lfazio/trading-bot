"""Alert channels — kill-switch state-change notifications.

REQ refs: REQ_SDS_INT_003 (AlertChannel Protocol; deliver KS state
changes to at least one configured channel; failure retried + logged),
REQ_SDD_ERR_005 (retry with exponential backoff up to 3 attempts;
failure does not block the calling module).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from trading_system.result import Err, Ok, Result

_MAX_ATTEMPTS = 3
_INITIAL_BACKOFF_SECONDS = 0.05  # short for tests; production caller can replace


@runtime_checkable
class AlertChannel(Protocol):
    """Single delivery attempt — caller controls retries."""

    def deliver(self, severity: str, payload: Mapping[str, Any]) -> Result[None, str]: ...


@dataclass(slots=True)
class MemoryAlertChannel:
    """In-memory test double; records every successful delivery."""

    delivered: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)

    def deliver(self, severity: str, payload: Mapping[str, Any]) -> Result[None, str]:
        self.delivered.append((severity, dict(payload)))
        return Ok(None)


@dataclass(slots=True)
class FlakyAlertChannel:
    """Test double that fails for the first N attempts, then succeeds.

    Useful for verifying the retry policy. ``fail_first`` counts how
    many attempts have been failed so far across the lifetime of the
    instance — calls are global, not per-message.
    """

    fail_first: int
    delivered: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    _attempts: int = 0

    def deliver(self, severity: str, payload: Mapping[str, Any]) -> Result[None, str]:
        self._attempts += 1
        if self._attempts <= self.fail_first:
            return Err(f"network:retry_pending: attempt {self._attempts} simulated failure")
        self.delivered.append((severity, dict(payload)))
        return Ok(None)


def deliver_with_retry(  # noqa: PLR0913 - retry policy needs each tunable
    channel: AlertChannel,
    severity: str,
    payload: Mapping[str, Any],
    *,
    max_attempts: int = _MAX_ATTEMPTS,
    initial_backoff_seconds: float = _INITIAL_BACKOFF_SECONDS,
    sleep: object = time.sleep,
) -> Result[None, str]:
    """Try to deliver ``payload``; retry on ``Err`` with exponential
    backoff up to ``max_attempts``. Returns the last ``Err`` if all
    attempts fail (REQ_SDD_ERR_005).

    ``sleep`` is injectable so tests can run without wall-clock waits.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    if initial_backoff_seconds < 0:
        raise ValueError(f"initial_backoff_seconds must be >= 0, got {initial_backoff_seconds}")

    last_err = "alert:no_attempts"
    backoff = initial_backoff_seconds
    sleep_callable: Any = sleep
    log = logging.getLogger(__name__)

    for attempt in range(1, max_attempts + 1):
        result = channel.deliver(severity, payload)
        match result:
            case Ok(_):
                return Ok(None)
            case Err(reason):
                last_err = reason
                log.warning(
                    "alert delivery attempt %d/%d failed: %s",
                    attempt,
                    max_attempts,
                    reason,
                )
                if attempt < max_attempts and backoff > 0:
                    sleep_callable(backoff)
                    backoff *= 2
    return Err(last_err)
