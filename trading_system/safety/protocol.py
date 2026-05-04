"""``SafetyLayer`` Protocol — surface the risk engine and other
upstream callers depend on.

The concrete state manager (``state_manager.py``) implements this
Protocol; tests can supply small in-memory doubles. Methods are kept
narrow so the Protocol is easy to satisfy and easy to verify.

REQ refs:
- REQ_SDS_MOD_010 — single writer; readers everywhere.
- REQ_S_KS_002 — kill switch overrides risk engine, strategy logic,
  execution layer.
- REQ_S_KS_011 — no module may execute trades while KILL is active.
- REQ_SDD_API_002 — runtime-checkable Protocol.
- REQ_SDD_API_003 — ``must_halt()`` is O(1), no locks, no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from trading_system.models.safety import KillSwitchState, KillSwitchTrigger


@runtime_checkable
class SafetyLayer(Protocol):
    """Read + escalate-only surface that engine modules use to query
    and influence the kill switch."""

    def must_halt(self) -> bool:
        """``True`` iff the kill switch is in ``KILL`` state. Engines
        call this in their gate-ordering chains (REQ_SDD_ALG_016).
        Implementations SHALL be O(1) and SHALL NOT acquire locks
        or perform I/O (REQ_SDD_API_003)."""
        ...

    def state(self) -> KillSwitchState:
        """Return the current kill-switch state. ``ACTIVE`` /
        ``DEGRADED`` / ``KILL``."""
        ...

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        """Escalate a trigger event to the state manager. The state
        manager decides whether the trigger advances the state and
        produces an audit snapshot."""
        ...
