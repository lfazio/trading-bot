"""Bundled notification channel adapters.

Phase A:
- ``LocalLogChannel`` — JSON-line writer; conformance baseline.
- ``MemoryNotificationChannel`` — in-memory test double.

Phase B (CR-001 + CR-018):
- ``EmailNotificationChannel`` — stdlib email + smtplib.
- ``SlackNotificationChannel`` — Slack incoming webhook via urllib
  (replaces the originally-scoped ``WhatsAppNotificationChannel``
  per CR-018; see Documentations/Change-Requests.md).
"""

from __future__ import annotations

from trading_system.notifications.channels.email import (
    DEFAULT_PASSWORD_ENV as EMAIL_DEFAULT_PASSWORD_ENV,
)
from trading_system.notifications.channels.email import (
    EmailNotificationChannel,
    render_email,
)
from trading_system.notifications.channels.local_log import (
    LocalLogChannel,
    MemoryNotificationChannel,
)
from trading_system.notifications.channels.slack import (
    DEFAULT_WEBHOOK_URL_ENV as SLACK_DEFAULT_WEBHOOK_URL_ENV,
)
from trading_system.notifications.channels.slack import (
    SlackNotificationChannel,
    render_block_kit,
)

__all__ = [
    "EMAIL_DEFAULT_PASSWORD_ENV",
    "EmailNotificationChannel",
    "LocalLogChannel",
    "MemoryNotificationChannel",
    "SLACK_DEFAULT_WEBHOOK_URL_ENV",
    "SlackNotificationChannel",
    "render_block_kit",
    "render_email",
]
