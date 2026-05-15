"""Tests for ``trading_system.portfolio_manager.proposal``.

Covers TC_PMG_001 (RebalanceProposal invariants) + TC_PMG_008
(Cadence Literal values).

REQ refs: REQ_F_PMG_002, REQ_F_PMG_006, REQ_SDD_PMG_001.
"""

from __future__ import annotations

from decimal import Decimal
from typing import get_args

import pytest

from trading_system.models.phase import AllocationBucket
from trading_system.portfolio_manager.proposal import (
    Cadence,
    RebalanceProposal,
)


def _proposal(**overrides: object) -> RebalanceProposal:
    defaults: dict[str, object] = {
        "bucket": AllocationBucket.STOCK,
        "current_pct": Decimal("0.65"),
        "target_pct": Decimal("0.60"),
        "drift": Decimal("0.05"),
        "direction": "decrease",
        "cadence": "monthly",
    }
    defaults.update(overrides)
    return RebalanceProposal(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TC_PMG_001 — invariants
# ---------------------------------------------------------------------------


def test_happy_path_construction() -> None:
    p = _proposal()
    assert p.bucket is AllocationBucket.STOCK
    assert p.direction == "decrease"
    assert p.cadence == "monthly"


def test_current_pct_must_lie_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="current_pct"):
        _proposal(current_pct=Decimal("-0.1"))
    with pytest.raises(ValueError, match="current_pct"):
        _proposal(current_pct=Decimal("1.5"))


def test_target_pct_must_lie_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="target_pct"):
        _proposal(target_pct=Decimal("-0.1"))
    with pytest.raises(ValueError, match="target_pct"):
        _proposal(target_pct=Decimal("1.5"))


def test_drift_must_match_current_minus_target() -> None:
    with pytest.raises(ValueError, match="drift must equal"):
        _proposal(
            current_pct=Decimal("0.65"),
            target_pct=Decimal("0.60"),
            drift=Decimal("0.10"),  # mismatch
        )


def test_direction_must_match_drift_sign() -> None:
    # Positive drift requires "decrease".
    with pytest.raises(ValueError, match="positive drift"):
        _proposal(
            current_pct=Decimal("0.65"),
            target_pct=Decimal("0.60"),
            drift=Decimal("0.05"),
            direction="increase",
        )
    # Negative drift requires "increase".
    with pytest.raises(ValueError, match="negative drift"):
        _proposal(
            current_pct=Decimal("0.55"),
            target_pct=Decimal("0.60"),
            drift=Decimal("-0.05"),
            direction="decrease",
        )


def test_dataclass_is_frozen() -> None:
    p = _proposal()
    with pytest.raises(Exception):
        p.current_pct = Decimal("0.5")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TC_PMG_008 — Cadence Literal
# ---------------------------------------------------------------------------


def test_cadence_literal_values() -> None:
    assert set(get_args(Cadence)) == {
        "intraday",
        "daily",
        "weekly",
        "monthly",
        "quarterly",
    }
