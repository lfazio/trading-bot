"""Bundled notification channel adapters.

Phase A ships:
- ``LocalLogChannel`` — JSON-line writer; conformance baseline.
- ``MemoryNotificationChannel`` — in-memory test double.

Phase B (deferred):
- ``EmailNotificationChannel`` — stdlib email + smtplib.
- ``WhatsAppNotificationChannel`` — WhatsApp Cloud API via urllib.
"""

from __future__ import annotations

from trading_system.notifications.channels.local_log import (
    LocalLogChannel,
    MemoryNotificationChannel,
)

__all__ = ["LocalLogChannel", "MemoryNotificationChannel"]
