"""``NotificationChannel`` + ``AlertChannel`` Protocols
(REQ_F_NOT_001 / REQ_SDS_NOT_001 / REQ_SDD_NOT_001).

The runtime sees one shape: a channel takes a ``NotificationPayload``
and returns ``Result[None, str]`` for a single delivery attempt.
Retry is the fan-out's job, not the channel's (REQ_NF_NOT_001 — the
trade-execution critical path SHALL stay off the notification
fan-out; the approval gate is the only synchronous exception).

``AlertChannel`` is the **narrowed sub-Protocol** the safety layer
consumes. Any concrete ``NotificationChannel`` that accepts
``KillSwitchEvent`` satisfies it structurally — Python's runtime
Protocol check sees the same ``deliver(payload)`` signature on both
sides.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from trading_system.notifications.payloads import (
    KillSwitchEvent,
    NotificationPayload,
)
from trading_system.result import Result


@runtime_checkable
class NotificationChannel(Protocol):
    """Single delivery attempt; retry is the fan-out's responsibility."""

    def deliver(self, payload: NotificationPayload) -> Result[None, str]: ...


@runtime_checkable
class AlertChannel(Protocol):
    """Narrowed Protocol consumed by ``safety/`` — accepts only
    KS events. Any ``NotificationChannel`` implementation whose
    runtime behaviour handles ``KillSwitchEvent`` structurally
    satisfies this Protocol."""

    def deliver(self, payload: KillSwitchEvent) -> Result[None, str]: ...
