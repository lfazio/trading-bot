"""Safety layer (kill switch).

This package will hold the full kill-switch implementation
(state manager, monitor, anomaly detector, alert system) — see
SDD §3.13 / §4.8 / §5.5. The Phase 5 step 9 risk engine depends on
the ``SafetyLayer`` Protocol defined here; the concrete state
manager lands in a follow-up step.

REQ refs:
- REQ_S_KS_001..012 — kill switch states, triggers, recovery.
- REQ_SDS_MOD_010 — single writer for state.
- REQ_SDD_API_002 — runtime-checkable Protocol.
- REQ_SDD_API_003 — ``must_halt()`` is O(1) with no I/O.
"""

from trading_system.safety.protocol import SafetyLayer

__all__ = ["SafetyLayer"]
