"""``TurboSelectorConfig`` — frozen filter + scoring parameters.

Defaults match SDD pseudo-code thresholds and ``config/turbos.yaml``:
knockout_min_distance 5 %, spread_max 1.5 %, weights
0.35 / 0.25 / 0.20 / 0.20 (REQ_SDD_CFG_004), threshold 0.50.

REQ refs: REQ_F_TRB_002, REQ_F_TRB_003, REQ_F_TRB_004,
REQ_SDD_CFG_004, REQ_SDS_INT_004 (frozen Config), REQ_SDD_API_004.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ALLOCATION_TOLERANCE = Decimal("1e-9")

# Default vol / liquidity windows — caller may override via the
# selector entry point but these are the SDD-aligned defaults.
DEFAULT_VOL_WINDOW = 30
DEFAULT_VOLUME_WINDOW = 20


@dataclass(frozen=True, slots=True)
class TurboSelectorConfig:
    """Filter cutoffs and scoring parameters for the turbo selector."""

    # Filter (REQ_F_TRB_002)
    knockout_min_distance: Decimal = Decimal("0.05")
    spread_max: Decimal = Decimal("0.015")
    min_liquidity: Decimal = Decimal("100000")
    max_volatility: Decimal = Decimal("0.50")

    # Scoring (REQ_F_TRB_003 / REQ_SDD_CFG_004)
    weights: tuple[Decimal, Decimal, Decimal, Decimal] = (
        Decimal("0.35"),
        Decimal("0.25"),
        Decimal("0.20"),
        Decimal("0.20"),
    )
    threshold: Decimal = Decimal("0.50")

    # Reference for leverage_efficiency_score normalization. Pinned
    # high enough that 5x and 10x leverages map to recognizable score
    # values; consumer YAML may override.
    leverage_efficiency_reference: Decimal = Decimal("20")

    # Sigmoid steepness for the knockout-distance score
    # (REQ_SDD_ALG_011). Higher k => sharper transition at the
    # threshold boundary.
    knockout_sigmoid_k: Decimal = Decimal("50")

    # Statistics windows
    vol_window: int = DEFAULT_VOL_WINDOW
    volume_window: int = DEFAULT_VOLUME_WINDOW

    def __post_init__(self) -> None:
        for label, v in (
            ("knockout_min_distance", self.knockout_min_distance),
            ("spread_max", self.spread_max),
            ("max_volatility", self.max_volatility),
            ("threshold", self.threshold),
        ):
            if not (Decimal(0) <= v <= Decimal(1)):
                raise ValueError(f"TurboSelectorConfig.{label} must lie in [0, 1], got {v}")
        if self.min_liquidity < 0:
            raise ValueError(
                f"TurboSelectorConfig.min_liquidity must be >= 0, got {self.min_liquidity}"
            )
        if self.leverage_efficiency_reference <= 0:
            raise ValueError(
                f"TurboSelectorConfig.leverage_efficiency_reference must be > 0, "
                f"got {self.leverage_efficiency_reference}"
            )
        if self.knockout_sigmoid_k <= 0:
            raise ValueError(
                f"TurboSelectorConfig.knockout_sigmoid_k must be > 0, got {self.knockout_sigmoid_k}"
            )
        if self.vol_window <= 0:
            raise ValueError(f"TurboSelectorConfig.vol_window must be > 0, got {self.vol_window}")
        if self.volume_window <= 0:
            raise ValueError(
                f"TurboSelectorConfig.volume_window must be > 0, got {self.volume_window}"
            )
        if any(w < 0 for w in self.weights):
            raise ValueError(f"TurboSelectorConfig.weights must all be >= 0, got {self.weights}")
        weight_sum = sum(self.weights, start=Decimal(0))
        if abs(weight_sum - Decimal(1)) > ALLOCATION_TOLERANCE:
            raise ValueError(
                f"TurboSelectorConfig.weights must sum to 1.0 +/- 1e-9, got {weight_sum}"
            )
