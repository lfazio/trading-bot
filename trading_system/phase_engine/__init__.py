"""Phase engine — capital-driven phase resolution and constraint dispatch.

REQ refs:
- REQ_F_CAP_002 — phase from ``equity + injected_capital``.
- REQ_F_CAP_003 — six phases (1..6).
- REQ_F_CAP_004 — phase boundaries from ``config/phases.yaml``.
- REQ_F_CAP_005 / REQ_SDD_ALG_002 — hysteresis on downgrade
  (default 10 % below the lower-phase upper bound).
- REQ_F_CAP_006..011 — per-phase constraints.
- REQ_F_CAP_012 — Phase 5+ portfolio vol cap.
- REQ_F_CAP_013 — phase-specific risk-per-trade band.
- REQ_SDS_FLO_002 — ``PhaseConstraints`` distributed consistently
  within a single tick to risk / strategies / turbo / SP consumers.
- REQ_SDS_MOD_004 — monotone-up by default; configurable hysteresis.
- REQ_SDD_PER_002 — ``resolve()`` runs in O(N) where N = number of
  phases (i.e., O(1) in practice).
"""

from trading_system.phase_engine.engine import (
    PhaseEngine,
    natural_phase_for_amount,
    resolve_with_hysteresis,
)
from trading_system.phase_engine.loader import (
    PhaseEngineLoadError,
    load_phase_engine,
)

__all__ = [
    "PhaseEngine",
    "PhaseEngineLoadError",
    "load_phase_engine",
    "natural_phase_for_amount",
    "resolve_with_hysteresis",
]
