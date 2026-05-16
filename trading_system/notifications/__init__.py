"""Remote notifications — CR-001 Phase 6.

Closed payload union: ``KillSwitchEvent`` / ``TradeApprovalRequest`` /
``ApprovalResponse`` / ``Summary`` / ``AnomalyAlert`` / ``Error``.
Channels conform to a ``NotificationChannel`` Protocol; the safety
layer's KS path uses the narrowed ``AlertChannel`` sub-Protocol
(REQ_F_NOT_003 — KS-only operator configs keep working).

Phase A (this slice) ships the in-process surface:
- Payload dataclasses (frozen + slotted; non-empty invariants).
- NotificationChannel + AlertChannel Protocols.
- LocalLogChannel adapter (always-available baseline; JSON-line
  writer over the structured-logging infrastructure from C3).
- NotificationFanOut with retry policy mirroring REQ_SDD_ERR_005.
- ApprovalGate with HMAC verification + default-deny timeout.
- SummaryPublisher render helper with byte-identical canonicalisation.

Phase B deferred (out of this slice):
- EmailNotificationChannel + WhatsAppNotificationChannel adapters.
- TradeApprovalAuditRepository persistence (CR-008 follow-up;
  ``0002_approvals.sql`` migration).
- ``safety/alert_system.py`` consuming NotificationFanOut (bridges
  the legacy `severity, payload` deliver shape to the new
  payload-typed shape).

REQ refs: REQ_F_NOT_001..008, REQ_NF_NOT_001..003, REQ_SDS_NOT_001..004,
REQ_SDD_NOT_001..008.
"""

from __future__ import annotations

from trading_system.notifications.approval import (
    ApprovalGate,
    ResponseInbox,
)
from trading_system.notifications.canonical import canonical_json_line
from trading_system.notifications.channel import (
    AlertChannel,
    NotificationChannel,
)
from trading_system.notifications.channels.local_log import (
    LocalLogChannel,
    MemoryNotificationChannel,
)
from trading_system.notifications.digest import (
    AnalyticsReader,
    PortfolioReader,
    RegistryReader,
    SummaryPublisher,
)
from trading_system.notifications.fanout import (
    NotificationFanOut,
    RetryPolicy,
)
from trading_system.notifications.payloads import (
    AnomalyAlert,
    ApprovalResponse,
    Error,
    KillSwitchEvent,
    NotificationPayload,
    RealizationLine,
    Summary,
    TradeApprovalRequest,
)

__all__ = [
    "AlertChannel",
    "AnalyticsReader",
    "AnomalyAlert",
    "ApprovalGate",
    "ApprovalResponse",
    "Error",
    "KillSwitchEvent",
    "LocalLogChannel",
    "MemoryNotificationChannel",
    "NotificationChannel",
    "NotificationFanOut",
    "NotificationPayload",
    "PortfolioReader",
    "RealizationLine",
    "RegistryReader",
    "ResponseInbox",
    "RetryPolicy",
    "Summary",
    "SummaryPublisher",
    "TradeApprovalRequest",
    "canonical_json_line",
]
