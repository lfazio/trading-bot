"""Tests for ``trading_system.portfolio_manager.attribution``.

Covers TC_PMG_006 (sum-to-NAV invariant) + TC_PMG_007 (multi-scope
decomposition row-shapes).

REQ refs: REQ_F_PMG_005, REQ_SDS_PMG_002, REQ_SDD_PMG_004,
REQ_SDD_ALG_020.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.identifiers import StrategyId
from trading_system.models.instrument import InstrumentClass
from trading_system.models.money import Currency, Money
from trading_system.portfolio_manager.attribution import (
    AttributionDecomposition,
    attribution_decomposition,
)


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), Currency.EUR)


def _decomposition(**overrides: object) -> AttributionDecomposition:
    defaults: dict[str, object] = {
        "by_strategy": {StrategyId("core"): _eur("100")},
        "by_sector": {"tech": _eur("60"), "financials": _eur("40")},
        "by_class": {InstrumentClass.STOCK: _eur("100")},
        "by_region": {"NL": _eur("60"), "FR": _eur("40")},
    }
    defaults.update(overrides)
    return AttributionDecomposition(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TC_PMG_006 — sum-to-NAV invariant
# ---------------------------------------------------------------------------


def test_consistent_scopes_construct_cleanly() -> None:
    d = _decomposition()
    assert d.by_class[InstrumentClass.STOCK] == _eur("100")


def test_by_strategy_sum_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="by_strategy"):
        _decomposition(
            by_strategy={StrategyId("core"): _eur("101")},  # NAV says 100
        )


def test_by_sector_sum_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="by_sector"):
        _decomposition(
            by_sector={"tech": _eur("60"), "financials": _eur("41")},
        )


def test_by_region_sum_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="by_region"):
        _decomposition(by_region={"NL": _eur("60"), "FR": _eur("41")})


def test_empty_by_class_raises() -> None:
    """``by_class`` is the NAV reference — cannot be empty unless the
    portfolio is empty (all scopes empty)."""
    with pytest.raises(ValueError, match="by_class"):
        AttributionDecomposition(
            by_strategy={},
            by_sector={},
            by_class={},
            by_region={},
        )


def test_currency_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="currency"):
        _decomposition(
            by_strategy={StrategyId("core"): Money(Decimal("100"), Currency.USD)},
        )


def test_tolerance_accepts_tiny_floating_point_drift() -> None:
    # Drift of 1e-10 is below the 1e-9 tolerance — should pass.
    d = _decomposition(
        by_strategy={StrategyId("core"): _eur("99.9999999999")},
    )
    assert d.by_class[InstrumentClass.STOCK] == _eur("100")


# ---------------------------------------------------------------------------
# TC_PMG_007 — multi-scope decomposition row-shapes
# ---------------------------------------------------------------------------


def test_helper_constructs_decomposition_from_aggregated_inputs() -> None:
    d = attribution_decomposition(
        by_strategy={StrategyId("core"): _eur("100")},
        by_sector={"tech": _eur("60"), "financials": _eur("40")},
        by_class={InstrumentClass.STOCK: _eur("100")},
        by_region={"NL": _eur("100")},
    )
    assert d.by_strategy[StrategyId("core")] == _eur("100")
    assert d.by_sector["tech"] == _eur("60")
    assert d.by_class[InstrumentClass.STOCK] == _eur("100")
    assert d.by_region["NL"] == _eur("100")


def test_other_sector_bucket_allowed() -> None:
    """`by_sector` may include 'other' for non-equity buckets."""
    d = _decomposition(
        by_sector={"tech": _eur("70"), "other": _eur("30")},
    )
    assert d.by_sector["other"] == _eur("30")
