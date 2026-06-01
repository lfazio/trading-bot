"""Notifications inbox — ring-buffered operator-visible event log.

REQ refs:
- REQ_F_WEB2_009 — notifications panel surfaces recent events.
- REQ_SDD_WEB2_010 — bounded deque at ``maxlen=100``; eviction is
  oldest-first when full.

Design:

The inbox is an in-memory ring buffer attached to ``app.state``.
Producers append; consumers read the snapshot. Append is
synchronous + thread-safe (Python's ``collections.deque`` is
atomic for ``appendleft``).

Two producer surfaces:

1. Direct ``append`` from the views layer (e.g., onboarding's
   finish handler logs ``paper_session_started``; the stop
   handler logs ``paper_session_stopped``).

2. A CR-001 ``NotificationFanOut`` subscriber that translates
   ``AnomalyAlert`` / ``Summary`` events to inbox entries —
   wired later when the live trading loop emits them.

Entries are immutable frozen dataclasses so the canonical-JSON
serialiser produces byte-identical output for equal inputs
(REQ_NF_WEB2_001).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal

from trading_system.result import Ok, Result


# Closed set — adding a category is a deliberate change here +
# the wiki amendment.
InboxSeverity = Literal["info", "warn", "error"]

INBOX_MAXLEN = 100


@dataclass(frozen=True, slots=True)
class InboxEntry:
    """A single operator-facing notification.

    ``category`` is a coarse tag for filtering ("paper-session",
    "anomaly", "summary", "ks"); ``code`` is the canonical
    short code ("session_started", "session_stopped",
    "degraded", etc); ``message`` is the human-readable body.
    """

    at: datetime
    category: str
    code: str
    severity: InboxSeverity
    message: str
    account_id: str = ""

    def __post_init__(self) -> None:
        if self.severity not in ("info", "warn", "error"):
            raise ValueError(
                f"InboxEntry.severity must be info|warn|error, got {self.severity!r}"
            )
        if not self.category.strip():
            raise ValueError("InboxEntry.category must be non-empty")
        if not self.code.strip():
            raise ValueError("InboxEntry.code must be non-empty")


@dataclass(slots=True)
class InboxChannel:
    """Bounded ring buffer for the notifications panel.

    Thread-safe (single ``Lock`` guards the underlying deque); the
    asyncio loop's append + the synchronous views layer's append
    + the SSE reader's snapshot all serialise through the lock.
    """

    maxlen: int = INBOX_MAXLEN
    _buffer: deque[InboxEntry] = field(init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.maxlen <= 0:
            raise ValueError(
                f"InboxChannel.maxlen must be > 0, got {self.maxlen}"
            )
        self._buffer = deque(maxlen=self.maxlen)

    def append(self, entry: InboxEntry) -> None:
        """Push a new entry. Oldest-first eviction when full."""
        with self._lock:
            self._buffer.append(entry)

    def snapshot(self) -> tuple[InboxEntry, ...]:
        """Return the buffer's current contents, newest LAST."""
        with self._lock:
            return tuple(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def deliver(self, payload: Any) -> Result[None, str]:
        """CR-001 ``NotificationChannel`` Protocol surface.

        Adapts every ``NotificationPayload`` variant
        (``KillSwitchEvent`` / ``AnomalyAlert`` / ``Summary`` /
        ``TradeApprovalRequest`` / ``ApprovalResponse`` /
        ``Error``) into an ``InboxEntry`` and appends it to the
        ring buffer. Returns ``Ok(None)`` unconditionally —
        the inbox is a best-effort visibility surface; an
        evicted older entry isn't an Err. The fan-out's retry
        contract therefore never kicks in for this channel
        (REQ_F_WEB2_009 — operator-visible event log, NOT a
        durable audit trail).
        """
        entry = _payload_to_inbox_entry(payload)
        self.append(entry)
        return Ok(None)


_PAYLOAD_SEVERITY: dict[str, InboxSeverity] = {
    "INFO": "info",
    "WARN": "warn",
    "URGENT": "error",
    "KILL": "error",
    "DEGRADE": "warn",
    "RECOVERY": "info",
}


def _payload_to_inbox_entry(payload: Any) -> InboxEntry:
    """Translate any ``NotificationPayload`` variant into an
    ``InboxEntry``. The mapping covers the closed payload union
    via duck-typed attribute access — adding a new payload type
    SHALL extend this helper + the test surface.

    Common fields the helper reads (where present):
    - ``at: datetime`` — entry timestamp; falls back to now.
    - ``code: str`` — InboxEntry.code.
    - ``severity: str`` — mapped to InboxEntry severity via
      ``_PAYLOAD_SEVERITY``; unknown values land as "info".
    - ``account_id`` — InboxEntry.account_id; empty string when
      the payload is household-scoped.
    - ``message: str`` — human-readable body; falls back to the
      type name.
    """
    at = getattr(payload, "at", None)
    if not isinstance(at, datetime):
        at = datetime.now(tz=UTC)

    code_raw = getattr(payload, "code", None)
    code = str(code_raw).strip() if code_raw else type(payload).__name__

    severity_raw = getattr(payload, "severity", None)
    severity_key = str(severity_raw).upper() if severity_raw is not None else "INFO"
    severity = _PAYLOAD_SEVERITY.get(severity_key, "info")

    account_id_raw = getattr(payload, "account_id", None)
    account_id = str(account_id_raw) if account_id_raw is not None else ""

    message_raw = getattr(payload, "message", None)
    message = str(message_raw) if message_raw else type(payload).__name__

    category = _payload_category(payload)

    return InboxEntry(
        at=at,
        category=category,
        code=code,
        severity=severity,
        message=message,
        account_id=account_id,
    )


def _payload_category(payload: Any) -> str:
    """Map payload type → InboxEntry.category. The set is closed
    over the NotificationPayload union; unknown types land under
    'notification'."""
    name = type(payload).__name__
    if name == "KillSwitchEvent":
        return "ks"
    if name == "AnomalyAlert":
        return "anomaly"
    if name == "Summary":
        return "summary"
    if name == "TradeApprovalRequest":
        return "approval"
    if name == "ApprovalResponse":
        return "approval"
    if name == "Error":
        return "error"
    return "notification"
