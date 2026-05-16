"""``NotificationFanOut`` — multi-channel dispatch with retry.

Mirrors the retry policy from ``trading_system.safety.alerts``
(REQ_SDD_ERR_005): exponential backoff, up to ``max_attempts``,
permanent failure logged but never raised. The trade-execution
critical path SHALL NOT block on the fan-out (REQ_NF_NOT_001) — the
approval gate is the only synchronous exception (see
``approval.py``).

Phase A ships a **serial** fan-out: one channel after another in
sorted-by-class-name order so test logs are deterministic. Phase B
upgrades to a thread-pool dispatcher; the public surface stays
backwards-compatible.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from trading_system.notifications.channel import NotificationChannel
from trading_system.notifications.payloads import NotificationPayload
from trading_system.result import Err, Ok


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential-backoff retry policy — REQ_SDD_ERR_005 family."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    growth_factor: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError(
                f"RetryPolicy.max_attempts must be > 0, got {self.max_attempts}"
            )
        if self.base_delay_seconds < 0:
            raise ValueError(
                f"RetryPolicy.base_delay_seconds must be >= 0, "
                f"got {self.base_delay_seconds}"
            )
        if self.growth_factor < 1.0:
            raise ValueError(
                f"RetryPolicy.growth_factor must be >= 1.0, "
                f"got {self.growth_factor}"
            )

    def delay_for(self, attempt: int) -> float:
        """Delay before attempt ``i`` (0-indexed). Returns 0 for the
        first attempt so the happy path doesn't pay an artificial
        sleep."""
        if attempt <= 0:
            return 0.0
        return self.base_delay_seconds * (self.growth_factor ** (attempt - 1))


@dataclass(slots=True)
class NotificationFanOut:
    """Dispatches one payload to every configured channel.

    Channels are visited in sorted-by-class-name order so two runs
    with the same channel set produce the same observation log
    (REQ_SDD_NOT_004). A permanent failure on one channel SHALL NOT
    affect the others.
    """

    channels: tuple[NotificationChannel, ...]
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    # Injectable sleeper for tests — production uses time.sleep.
    sleep: Callable[[float], None] = field(default_factory=lambda: time.sleep)

    def dispatch(self, payload: NotificationPayload) -> None:
        """Fire-and-forget: every channel is given ``max_attempts``
        tries with backoff between attempts; the caller never sees
        per-channel failures (they're logged structured)."""
        for ch in self._ordered_channels():
            self._deliver_with_retry(ch, payload)

    def _ordered_channels(self) -> tuple[NotificationChannel, ...]:
        return tuple(
            sorted(self.channels, key=lambda c: c.__class__.__name__)
        )

    def _deliver_with_retry(
        self, ch: NotificationChannel, payload: NotificationPayload
    ) -> None:
        for attempt in range(self.retry_policy.max_attempts):
            delay = self.retry_policy.delay_for(attempt)
            if delay > 0:
                self.sleep(delay)
            result = ch.deliver(payload)
            match result:
                case Ok(_):
                    return
                case Err(reason):
                    _LOG.warning(
                        "notification delivery failed",
                        extra={
                            "category": "notification",
                            "payload": {
                                "channel": ch.__class__.__name__,
                                "payload_kind": type(payload).__name__,
                                "attempt": attempt,
                                "reason": reason,
                            },
                        },
                    )
        # All attempts failed — permanent. The trading critical path
        # is unaffected (REQ_NF_NOT_001); persistence of the failure
        # row is Phase B (TradeApprovalAuditRepository / similar).
        _LOG.error(
            "notification permanently failed",
            extra={
                "category": "notification",
                "payload": {
                    "channel": ch.__class__.__name__,
                    "payload_kind": type(payload).__name__,
                    "max_attempts": self.retry_policy.max_attempts,
                },
            },
        )
