"""``PerformanceMetrics`` — input shape for ``MilestoneController.evaluate``.

The analytics layer populates this; the controller is pure on it.
Boolean fields encode the operator's policy thresholds (e.g.,
"low_drawdown" means analytics already compared the running DD
against the phase cap and returned True). The numeric fields drive
the fake-growth detector.

REQ refs: REQ_F_MIL_002 (gating booleans), REQ_F_MIL_004 +
REQ_SDD_ALG_015 (fake-growth numeric thresholds), REQ_NF_LOG_001
(timestamped origin).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Frozen snapshot consumed by the milestone controller."""

    # Gating booleans (REQ_F_MIL_002)
    stable_returns: bool
    low_drawdown: bool
    strategy_consistency: bool

    # Fake-growth detector inputs (REQ_SDD_ALG_015)
    gain_30d: Decimal  # fractional return over the trailing 30 days
    largest_trade_pct: Decimal  # share of capital in the largest single trade
    realized_vol: Decimal  # current annualized vol
    rolling_vol_avg: Decimal  # trailing average annualized vol

    def __post_init__(self) -> None:
        if self.realized_vol < 0:
            raise ValueError(
                f"PerformanceMetrics.realized_vol must be >= 0, got {self.realized_vol}"
            )
        if self.rolling_vol_avg < 0:
            raise ValueError(
                f"PerformanceMetrics.rolling_vol_avg must be >= 0, got {self.rolling_vol_avg}"
            )
        if not (Decimal(0) <= self.largest_trade_pct <= Decimal(1)):
            raise ValueError(
                f"PerformanceMetrics.largest_trade_pct must lie in [0, 1], "
                f"got {self.largest_trade_pct}"
            )
