"""``OverlayPolicy`` frozen dataclass — REQ_F_HOV_004.

Hard ≤ 10 % ``max_overlay_pct`` ceiling per REQ_F_CAP_011 — operators
may tighten the cap but SHALL NOT loosen it. Defaults pinned to the
SDD: ``target_beta=0.5`` / ``target_vol=0.12`` / ``beta_band=0.05`` /
``hedge_ratio=1.0`` / ``rebalance_frequency="weekly"`` /
``max_overlay_pct=0.10`` / ``benchmark="EUROSTOXX50"`` /
``carry_pct_per_year=0.005``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class OverlayPolicy:
    """Frozen policy bag consumed by ``HedgeOverlay.size`` +
    ``OverlayLedger.carry_cost``."""

    target_beta: Decimal = Decimal("0.5")
    target_vol: Decimal = Decimal("0.12")
    beta_band: Decimal = Decimal("0.05")
    hedge_ratio: Decimal = Decimal("1.0")
    rebalance_frequency: Literal["daily", "weekly", "monthly"] = "weekly"
    max_overlay_pct: Decimal = Decimal("0.10")
    benchmark: str = "EUROSTOXX50"
    carry_pct_per_year: Decimal = Decimal("0.005")

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.target_beta <= Decimal("2")):
            raise ValueError(
                f"hov:target_beta_out_of_bounds: must be in [0, 2], "
                f"got {self.target_beta}"
            )
        if not (Decimal("0") < self.target_vol <= Decimal("1")):
            raise ValueError(
                f"hov:target_vol_out_of_bounds: must be in (0, 1], "
                f"got {self.target_vol}"
            )
        if not (Decimal("0") < self.beta_band <= Decimal("0.5")):
            raise ValueError(
                f"hov:beta_band_out_of_bounds: must be in (0, 0.5], "
                f"got {self.beta_band}"
            )
        if not (Decimal("0") < self.hedge_ratio <= Decimal("1")):
            raise ValueError(
                f"hov:hedge_ratio_out_of_bounds: must be in (0, 1], "
                f"got {self.hedge_ratio}"
            )
        # Hard phase-6 ceiling per REQ_F_CAP_011 — tighten OK, loosen NOT.
        if not (Decimal("0") < self.max_overlay_pct <= Decimal("0.10")):
            raise ValueError(
                f"hov:max_overlay_pct_exceeds_phase6_cap: must be in "
                f"(0, 0.10] per REQ_F_CAP_011, got {self.max_overlay_pct}"
            )
        if self.carry_pct_per_year < 0:
            raise ValueError(
                f"hov:carry_pct_negative: must be >= 0, "
                f"got {self.carry_pct_per_year}"
            )
        if not self.benchmark.strip():
            raise ValueError("hov:benchmark_empty: benchmark must be non-empty")
