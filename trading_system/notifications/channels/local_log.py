"""``LocalLogChannel`` + ``MemoryNotificationChannel``.

``LocalLogChannel(path)`` writes one canonical-JSON line per
delivered payload to the configured path. It's the always-available
conformance baseline — every deployment ships with at least this
channel so the fan-out is never empty (REQ_F_NOT_002).

``MemoryNotificationChannel()`` is the in-memory test double used
by both the notifications test suite and downstream packages
that need a `NotificationChannel` fixture (mirrors
``safety.alerts.MemoryAlertChannel``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from trading_system.notifications.canonical import canonical_json_line
from trading_system.notifications.payloads import NotificationPayload
from trading_system.result import Err, Ok, Result


@dataclass(slots=True)
class LocalLogChannel:
    """JSON-line file writer — REQ_F_NOT_002 conformance baseline."""

    path: Path

    def __post_init__(self) -> None:
        # Resolve the parent directory once so the deliver path stays
        # ``O(1)``; mkdir is intentionally caller-visible (no silent
        # auto-creation here) to keep the I/O boundary explicit.
        if not isinstance(self.path, Path):
            self.path = Path(self.path)

    def deliver(self, payload: NotificationPayload) -> Result[None, str]:
        """Write one canonical-JSON line + a trailing newline.

        Returns ``Err("notifications:io:<reason>")`` on any
        ``OSError`` so the fan-out's retry policy can fire.
        """
        line = canonical_json_line(payload)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as e:
            return Err(f"notifications:io:{e!s}")
        return Ok(None)


@dataclass(slots=True)
class MemoryNotificationChannel:
    """In-memory test double; records every successful delivery."""

    delivered: list[NotificationPayload] = field(default_factory=list)

    def deliver(self, payload: NotificationPayload) -> Result[None, str]:
        self.delivered.append(payload)
        return Ok(None)
