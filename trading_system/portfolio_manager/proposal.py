"""Proposal row shapes for the portfolio manager.

REQ refs: REQ_F_PMG_002, REQ_F_PMG_006, REQ_SDD_PMG_001.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.models.phase import AllocationBucket

# Schedule cadence carried on every proposal so the runtime
# scheduler fans them out at the right frequency (REQ_F_PMG_006).
Cadence = Literal["intraday", "daily", "weekly", "monthly", "quarterly"]

RebalanceDirection = Literal["increase", "decrease"]


@dataclass(frozen=True, slots=True)
class RebalanceProposal:
    """Higher-level intent: bring an allocation bucket's exposure back
    to its phase target.

    The Phase-6 sizer (runtime wiring) converts each rebalance
    proposal into per-instrument ``TradeProposal`` rows; v1 ships the
    intent and lets the downstream caller decide the sizing math.
    """

    bucket: AllocationBucket
    current_pct: Decimal
    target_pct: Decimal
    drift: Decimal               # current - target (signed)
    direction: RebalanceDirection
    cadence: Cadence

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.current_pct <= Decimal(1)):
            raise ValueError(
                "RebalanceProposal.current_pct must lie in [0, 1], "
                f"got {self.current_pct}"
            )
        if not (Decimal(0) <= self.target_pct <= Decimal(1)):
            raise ValueError(
                "RebalanceProposal.target_pct must lie in [0, 1], "
                f"got {self.target_pct}"
            )
        # ``drift`` SHALL match ``current_pct - target_pct`` exactly.
        expected_drift = self.current_pct - self.target_pct
        if self.drift != expected_drift:
            raise ValueError(
                f"RebalanceProposal.drift must equal current_pct - "
                f"target_pct ({expected_drift}), got {self.drift}"
            )
        # The direction SHALL match the sign of drift.
        if self.drift > 0 and self.direction != "decrease":
            raise ValueError(
                "RebalanceProposal: positive drift requires "
                f"direction='decrease', got {self.direction!r}"
            )
        if self.drift < 0 and self.direction != "increase":
            raise ValueError(
                "RebalanceProposal: negative drift requires "
                f"direction='increase', got {self.direction!r}"
            )
