"""TC_HOV_001 — ``OverlayPolicy`` invariants.

REQ refs: REQ_F_HOV_004, REQ_F_CAP_011 (hard ≤ 10 % ceiling).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.institutional.hedge_overlay import OverlayPolicy


def test_defaults_match_sdd() -> None:
    """REQ_F_HOV_004 — documented defaults pin to the SDD."""
    p = OverlayPolicy()
    assert p.target_beta == Decimal("0.5")
    assert p.target_vol == Decimal("0.12")
    assert p.beta_band == Decimal("0.05")
    assert p.hedge_ratio == Decimal("1.0")
    assert p.rebalance_frequency == "weekly"
    assert p.max_overlay_pct == Decimal("0.10")
    assert p.benchmark == "EUROSTOXX50"
    assert p.carry_pct_per_year == Decimal("0.005")


def test_target_beta_out_of_bounds() -> None:
    with pytest.raises(ValueError, match="hov:target_beta_out_of_bounds"):
        OverlayPolicy(target_beta=Decimal("2.01"))


def test_target_vol_zero_rejected() -> None:
    with pytest.raises(ValueError, match="hov:target_vol_out_of_bounds"):
        OverlayPolicy(target_vol=Decimal("0"))


def test_beta_band_out_of_bounds() -> None:
    with pytest.raises(ValueError, match="hov:beta_band_out_of_bounds"):
        OverlayPolicy(beta_band=Decimal("0.6"))


def test_hedge_ratio_above_one_rejected() -> None:
    with pytest.raises(ValueError, match="hov:hedge_ratio_out_of_bounds"):
        OverlayPolicy(hedge_ratio=Decimal("1.5"))


def test_max_overlay_pct_above_phase6_cap_rejected() -> None:
    """REQ_F_CAP_011 hard ceiling — operators MAY tighten but NOT loosen."""
    with pytest.raises(ValueError, match="hov:max_overlay_pct_exceeds_phase6_cap"):
        OverlayPolicy(max_overlay_pct=Decimal("0.15"))


def test_max_overlay_pct_tighter_than_ceiling_allowed() -> None:
    """Operators MAY tighten the cap below 10 %."""
    p = OverlayPolicy(max_overlay_pct=Decimal("0.05"))
    assert p.max_overlay_pct == Decimal("0.05")


def test_carry_pct_negative_rejected() -> None:
    with pytest.raises(ValueError, match="hov:carry_pct_negative"):
        OverlayPolicy(carry_pct_per_year=Decimal("-0.001"))


def test_benchmark_empty_rejected() -> None:
    with pytest.raises(ValueError, match="hov:benchmark_empty"):
        OverlayPolicy(benchmark="   ")
