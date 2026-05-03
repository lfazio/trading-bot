"""Turbo (knockout-leveraged certificate) selector.

Filter -> score -> select pipeline. Phase-gated: when
``PhaseConstraints.turbo_exposure_max == 0`` the selector emits
``Nothing()`` regardless of how the candidates score, enforcing
``REQ_F_CAP_006`` (turbos disabled in Phase 1) at the selection
boundary.

REQ refs:
- REQ_F_TRB_001 — three-step pipeline.
- REQ_F_TRB_002 — filter cutoffs (knockout < 5%, spread > 1.5%,
  leverage above phase cap, low liquidity, extreme vol).
- REQ_F_TRB_003 — scoring weights ``0.35 / 0.25 / 0.20 / 0.20``.
- REQ_F_TRB_004 — below-threshold best => no trade.
- REQ_F_TRB_005 — risk = invested capital only (the selector emits
  candidates; the broker / portfolio enforces this elsewhere).
- REQ_F_TRB_006 — every candidate carries underlying / direction /
  leverage / knockout / spread (validated at ``Turbo`` construction).
- REQ_SDD_ALG_011 — knockout-distance score is a sigmoid centred at
  the minimum-distance threshold.
- REQ_SDD_CFG_004 — default scoring weights live in ``turbos.yaml``.
"""

from trading_system.turbo_selector.config import TurboSelectorConfig
from trading_system.turbo_selector.engine import (
    ScoredTurbo,
    TurboCandidate,
    TurboScore,
    select,
)
from trading_system.turbo_selector.loader import (
    TurboSelectorLoadError,
    load_turbo_selector_config,
)
from trading_system.turbo_selector.score import (
    cost_score,
    expected_move_capture_score,
    knockout_distance_score,
    leverage_efficiency_score,
)
from trading_system.turbo_selector.stats import avg_volume, realized_vol

__all__ = [
    "ScoredTurbo",
    "TurboCandidate",
    "TurboScore",
    "TurboSelectorConfig",
    "TurboSelectorLoadError",
    "avg_volume",
    "cost_score",
    "expected_move_capture_score",
    "knockout_distance_score",
    "leverage_efficiency_score",
    "load_turbo_selector_config",
    "realized_vol",
    "select",
]
