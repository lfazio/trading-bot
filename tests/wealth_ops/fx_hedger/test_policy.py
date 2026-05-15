"""Tests for ``trading_system.wealth_ops.fx_hedger.policy``.

Covers TC_FXH_001 (HedgePolicy invariants + documented defaults).

REQ refs: REQ_F_FXH_004.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.wealth_ops.fx_hedger.policy import HedgePolicy


def test_default_constants_match_srs_defaults() -> None:
    p = HedgePolicy()
    assert p.threshold_pct == Decimal("0.05")
    assert p.target_hedge_ratio == Decimal("0.80")
    assert p.rebalance_frequency == "monthly"


def test_threshold_pct_must_lie_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="threshold_pct"):
        HedgePolicy(threshold_pct=Decimal("-0.01"))
    with pytest.raises(ValueError, match="threshold_pct"):
        HedgePolicy(threshold_pct=Decimal("1.01"))


def test_target_hedge_ratio_must_lie_in_open_unit_interval() -> None:
    with pytest.raises(ValueError, match="target_hedge_ratio"):
        HedgePolicy(target_hedge_ratio=Decimal("0"))
    with pytest.raises(ValueError, match="target_hedge_ratio"):
        HedgePolicy(target_hedge_ratio=Decimal("-0.1"))
    with pytest.raises(ValueError, match="target_hedge_ratio"):
        HedgePolicy(target_hedge_ratio=Decimal("1.01"))
    # 1.0 is the upper bound — allowed.
    HedgePolicy(target_hedge_ratio=Decimal("1.0"))


def test_threshold_zero_is_allowed() -> None:
    p = HedgePolicy(threshold_pct=Decimal("0"))
    assert p.threshold_pct == Decimal("0")


def test_dataclass_is_frozen() -> None:
    p = HedgePolicy()
    with pytest.raises(Exception):
        p.threshold_pct = Decimal("0.10")  # type: ignore[misc]
