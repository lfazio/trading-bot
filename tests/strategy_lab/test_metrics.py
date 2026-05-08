"""Tests for ``trading_system.strategy_lab.metrics``.

REQ refs: REQ_F_MTO_003, REQ_F_MTO_006, REQ_F_MTO_008.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.strategy_lab.metrics import StrategyMetrics


def _metrics(**overrides) -> StrategyMetrics:
    base = dict(
        net_after_tax_return=Decimal("0.10"),
        sharpe=Decimal("1.5"),
        stability=Decimal("0.7"),
        dd_penalty=Decimal("0.1"),
        max_drawdown=Decimal("0.1"),
        turnover=Decimal("12"),
        regime_stability=Decimal("0.6"),
        leverage=Decimal("1"),
        parameter_sensitivity=Decimal("0.2"),
        risk=Decimal("0.15"),
        return_=Decimal("0.10"),
    )
    base.update(overrides)
    return StrategyMetrics(**base)


def test_construction_default_values_pass() -> None:
    m = _metrics()
    assert m.sharpe == Decimal("1.5")


@pytest.mark.parametrize(
    "field",
    ["stability", "dd_penalty", "max_drawdown", "regime_stability", "parameter_sensitivity"],
)
def test_unit_interval_fields_rejected_outside_zero_one(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _metrics(**{field: Decimal("1.5")})


def test_negative_turnover_rejected() -> None:
    with pytest.raises(ValueError, match="turnover"):
        _metrics(turnover=Decimal("-1"))


def test_negative_leverage_rejected() -> None:
    with pytest.raises(ValueError, match="leverage"):
        _metrics(leverage=Decimal("-0.5"))


def test_negative_risk_rejected() -> None:
    with pytest.raises(ValueError, match="risk"):
        _metrics(risk=Decimal("-0.01"))
