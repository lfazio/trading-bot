"""``HedgePolicy`` — frozen parameters for the FX hedger.

Loaded once from ``config/fx_hedger.yaml`` at startup. Defaults work
without operator configuration. Runtime mutation is forbidden
(REQ_SDS_INT_004 pattern).

REQ refs: REQ_F_FXH_004.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class HedgePolicy:
    """Threshold + sizing parameters for ``FXHedger``.

    Defaults match the SRS-pinned values:
    - ``threshold_pct = 0.05`` — hedge non-base currencies above 5%
      of household equity.
    - ``target_hedge_ratio = 0.80`` — hedge 80% of the above-threshold
      exposure.
    - ``rebalance_frequency = "monthly"`` — operator-driven cadence.
    """

    threshold_pct: Decimal = Decimal("0.05")
    target_hedge_ratio: Decimal = Decimal("0.80")
    rebalance_frequency: Literal["daily", "weekly", "monthly"] = "monthly"

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.threshold_pct <= Decimal(1)):
            raise ValueError(
                "HedgePolicy.threshold_pct must lie in [0, 1], "
                f"got {self.threshold_pct}"
            )
        if not (Decimal(0) < self.target_hedge_ratio <= Decimal(1)):
            raise ValueError(
                "HedgePolicy.target_hedge_ratio must lie in (0, 1], "
                f"got {self.target_hedge_ratio}"
            )
