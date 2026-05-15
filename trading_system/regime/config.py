"""``RegimeConfig`` — frozen parameters for the regime detector.

Loaded once at startup from ``config/regime.yaml``; runtime mutation
is forbidden (REQ_SDS_INT_004 / REQ_F_RGM_006). Absent file ⇒ documented
defaults so backtests / tests work without operator configuration.

REQ refs: REQ_F_RGM_002, REQ_F_RGM_006, REQ_NF_RGM_001.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    """Detector + tracker configuration.

    Defaults match the SRS-documented numbers (REQ_F_RGM_002 /
    REQ_F_RGM_006). The constructor enforces invariants so a
    misconfigured YAML fails fast at load time rather than producing
    a malformed regime at runtime.
    """

    ma_short: int = 50
    ma_long: int = 200
    vol_window: int = 60
    vol_high_percentile: Decimal = Decimal("0.90")
    vol_low_percentile: Decimal = Decimal("0.75")
    sideways_threshold: Decimal = Decimal("0.02")
    confirmation_periods: int = 2
    bar_source: str = "synthetic_eu"

    def __post_init__(self) -> None:
        if self.ma_short <= 0:
            raise ValueError(f"RegimeConfig.ma_short must be > 0, got {self.ma_short}")
        if self.ma_long <= 0:
            raise ValueError(f"RegimeConfig.ma_long must be > 0, got {self.ma_long}")
        if self.ma_short >= self.ma_long:
            raise ValueError(
                "RegimeConfig.ma_short "
                f"({self.ma_short}) must be < ma_long ({self.ma_long})"
            )
        if self.vol_window <= 1:
            raise ValueError(
                f"RegimeConfig.vol_window must be > 1, got {self.vol_window}"
            )
        if not (Decimal(0) <= self.vol_low_percentile <= Decimal(1)):
            raise ValueError(
                f"RegimeConfig.vol_low_percentile must be in [0, 1], "
                f"got {self.vol_low_percentile}"
            )
        if not (Decimal(0) <= self.vol_high_percentile <= Decimal(1)):
            raise ValueError(
                f"RegimeConfig.vol_high_percentile must be in [0, 1], "
                f"got {self.vol_high_percentile}"
            )
        if self.vol_low_percentile > self.vol_high_percentile:
            raise ValueError(
                "RegimeConfig.vol_low_percentile "
                f"({self.vol_low_percentile}) must be <= "
                f"vol_high_percentile ({self.vol_high_percentile})"
            )
        if not (Decimal(0) <= self.sideways_threshold <= Decimal(1)):
            raise ValueError(
                f"RegimeConfig.sideways_threshold must be in [0, 1], "
                f"got {self.sideways_threshold}"
            )
        if self.confirmation_periods < 1:
            raise ValueError(
                "RegimeConfig.confirmation_periods must be >= 1, "
                f"got {self.confirmation_periods}"
            )
        if not self.bar_source.strip():
            raise ValueError("RegimeConfig.bar_source must be non-empty")
