"""``ScreenerConfig`` — frozen filter + scoring parameters.

Defaults match the SRS (REQ_F_SCR_001) thresholds: yield 3-7 %,
payout < 70 %, D/E < 1.5, dividend history >= 5 years. Score weights
default to (stability 0.5, yield_quality 0.3, valuation 0.2) per the
SDD §4.4 pseudo-code.

REQ refs: REQ_F_SCR_001, REQ_F_SCR_002, REQ_SDS_INT_004 (frozen
config), REQ_SDD_API_004.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ALLOCATION_TOLERANCE = Decimal("1e-9")


@dataclass(frozen=True, slots=True)
class ScreenerConfig:
    """Screener thresholds and score weights."""

    yield_min: Decimal = Decimal("0.03")
    yield_max: Decimal = Decimal("0.07")
    payout_max: Decimal = Decimal("0.70")
    debt_equity_max: Decimal = Decimal("1.5")
    min_history_years: int = 5
    weights: tuple[Decimal, Decimal, Decimal] = (
        Decimal("0.5"),
        Decimal("0.3"),
        Decimal("0.2"),
    )
    stability_full_years: int = 20  # history >= this => max stability score

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.yield_min <= self.yield_max):
            raise ValueError(
                f"ScreenerConfig: 0 <= yield_min <= yield_max required, "
                f"got ({self.yield_min}, {self.yield_max})"
            )
        if self.yield_max > Decimal(1):
            raise ValueError(f"ScreenerConfig.yield_max must be <= 1.0, got {self.yield_max}")
        if not (Decimal(0) < self.payout_max <= Decimal(1)):
            raise ValueError(f"ScreenerConfig.payout_max must lie in (0, 1], got {self.payout_max}")
        if self.debt_equity_max <= 0:
            raise ValueError(
                f"ScreenerConfig.debt_equity_max must be > 0, got {self.debt_equity_max}"
            )
        if self.min_history_years < 0:
            raise ValueError(
                f"ScreenerConfig.min_history_years must be >= 0, got {self.min_history_years}"
            )
        if self.stability_full_years <= 0:
            raise ValueError(
                f"ScreenerConfig.stability_full_years must be > 0, got {self.stability_full_years}"
            )
        if any(w < 0 for w in self.weights):
            raise ValueError(f"ScreenerConfig.weights must all be >= 0, got {self.weights}")
        weight_sum = sum(self.weights, start=Decimal(0))
        if abs(weight_sum - Decimal(1)) > ALLOCATION_TOLERANCE:
            raise ValueError(f"ScreenerConfig.weights must sum to 1.0 +/- 1e-9, got {weight_sum}")
