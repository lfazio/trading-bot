"""Milestone-driven gradual exposure unlock with a fake-growth guard.

Per REQ_F_MIL_001..004 the controller exposes:

- A configurable milestone ladder (default
  ``[2k, 5k, 10k, 20k, 50k, 100k, 200k, 500k, 1M, 2M, 5M]`` EUR).
- A crossing evaluator that requires every gating condition (stable
  returns AND low drawdown AND strategy consistency AND no recent
  kill-switch event AND no fake-growth signal).
- A fake-growth detector (REQ_SDD_ALG_015) that rejects scaling
  driven by 30d gain >30%, single-trade-share >50%, or realized
  vol >2x rolling.
- A gradual scaling output (10-20% exposure unlock) — exponential
  / leverage-explosion scaling is unrepresentable.

REQ refs: REQ_F_MIL_001..004, REQ_SDS_MOD_012, REQ_SDD_ALG_015.
"""

from trading_system.milestone_controller.controller import (
    DEFAULT_MILESTONES,
    MilestoneConfig,
    MilestoneController,
    MilestoneCrossing,
)
from trading_system.milestone_controller.metrics import PerformanceMetrics

__all__ = [
    "DEFAULT_MILESTONES",
    "MilestoneConfig",
    "MilestoneController",
    "MilestoneCrossing",
    "PerformanceMetrics",
]
