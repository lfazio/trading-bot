"""Hedge Overlay Manager (CR-012, Phase 5 implementation).

v1 ships **index futures only** (linear delta hedge) targeting an
EUR-denominated portfolio against the EuroSTOXX 50 benchmark.
``HedgeOverlay.size`` is pure and phase-gated — sub-phase-6 calls
return ``Ok(())`` before reading any other input.

Public surface re-exports:
- ``compute_portfolio_beta`` — pure rolling beta (REQ_F_HOV_002)
- ``HedgeOverlay`` — pure sizer (REQ_F_HOV_003)
- ``OverlayPolicy`` — frozen policy bag with hard ≤ 10 %
  ``max_overlay_pct`` ceiling per REQ_F_CAP_011 (REQ_F_HOV_004)
- ``OverlayLedger`` — append-only OPEN→CLOSED cursor with
  deterministic mark + carry formulas + tax accessors (REQ_F_HOV_005)
- ``OverlayProposal`` / ``IndexFuturePosition`` /
  ``OverlayPositionState`` — frozen row types
- ``OverlayError`` — closed category set

REQ refs: REQ_F_HOV_001..005, REQ_NF_HOV_001, REQ_SDS_HOV_001..002,
REQ_SDD_HOV_001..004.
"""

from __future__ import annotations

from trading_system.institutional.hedge_overlay.errors import OverlayError
from trading_system.institutional.hedge_overlay.exposure import compute_portfolio_beta
from trading_system.institutional.hedge_overlay.instruments import (
    IndexFuturePosition,
    OverlayPositionState,
    OverlayProposal,
)
from trading_system.institutional.hedge_overlay.ledger import OverlayLedger
from trading_system.institutional.hedge_overlay.overlay import HedgeOverlay
from trading_system.institutional.hedge_overlay.policy import OverlayPolicy

__all__ = [
    "HedgeOverlay",
    "IndexFuturePosition",
    "OverlayError",
    "OverlayLedger",
    "OverlayPolicy",
    "OverlayPositionState",
    "OverlayProposal",
    "compute_portfolio_beta",
]
