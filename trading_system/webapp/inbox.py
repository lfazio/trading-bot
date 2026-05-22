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
from datetime import datetime
from threading import Lock
from typing import Literal


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
