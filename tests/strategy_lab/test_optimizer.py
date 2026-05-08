"""Tests for ``trading_system.strategy_lab.optimizer``.

REQ refs: REQ_F_MTO_006 (safe self-improvement: new_risk <=
baseline_risk AND new_return/risk > baseline).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.optimizer import Optimizer, OptimizerConfig


def _metrics(risk: str, ret: str) -> StrategyMetrics:
    return StrategyMetrics(
        net_after_tax_return=Decimal(ret),
        sharpe=Decimal("1.0"),
        stability=Decimal("0.5"),
        dd_penalty=Decimal("0.1"),
        max_drawdown=Decimal("0.1"),
        turnover=Decimal("0"),
        regime_stability=Decimal("0.5"),
        leverage=Decimal("1"),
        parameter_sensitivity=Decimal("0.2"),
        risk=Decimal(risk),
        return_=Decimal(ret),
    )


def _opt(top_k: int = 3) -> Optimizer:
    return Optimizer(cfg=OptimizerConfig(top_k=top_k))


def test_top_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="top_k"):
        OptimizerConfig(top_k=0)


class TestColdStart:
    def test_no_baseline_accepts_top_k(self) -> None:
        ranked = [
            ("c1", _metrics("0.1", "0.2"), Decimal("0.5")),
            ("c2", _metrics("0.2", "0.1"), Decimal("0.4")),
            ("c3", _metrics("0.3", "0.05"), Decimal("0.3")),
            ("c4", _metrics("0.4", "0.01"), Decimal("0.2")),
        ]
        out = _opt(top_k=3).accept(ranked, baseline=None)
        # First three accepted; fourth marked below_top_k.
        assert [d.accepted for _, d in out] == [True, True, True, False]
        assert out[3][1].reason == "below_top_k"


class TestWithBaseline:
    def test_rejects_higher_risk(self) -> None:
        baseline = _metrics(risk="0.10", ret="0.10")
        candidate = _metrics(risk="0.15", ret="0.20")  # better return BUT higher risk
        ranked = [("c1", candidate, Decimal("0.5"))]
        out = _opt().accept(ranked, baseline=baseline)
        assert out[0][1].accepted is False
        assert out[0][1].reason == "risk_higher_than_baseline"

    def test_rejects_equal_risk_adjusted_return(self) -> None:
        baseline = _metrics(risk="0.10", ret="0.10")
        # Same return, same risk -> ratio equal, not strictly improved.
        candidate = _metrics(risk="0.10", ret="0.10")
        out = _opt().accept([("c1", candidate, Decimal("0.5"))], baseline=baseline)
        assert out[0][1].accepted is False
        assert out[0][1].reason == "return_per_risk_not_improved"

    def test_accepts_strictly_improved_risk_adjusted_return(self) -> None:
        baseline = _metrics(risk="0.10", ret="0.10")  # ratio 1.0
        candidate = _metrics(risk="0.10", ret="0.15")  # ratio 1.5, same risk
        out = _opt().accept([("c1", candidate, Decimal("0.5"))], baseline=baseline)
        assert out[0][1].accepted is True

    def test_accepts_lower_risk_better_ratio(self) -> None:
        baseline = _metrics(risk="0.10", ret="0.10")  # ratio 1.0
        # Lower risk AND higher return -> ratio improves and risk caps OK.
        candidate = _metrics(risk="0.05", ret="0.08")  # ratio 1.6
        out = _opt().accept([("c1", candidate, Decimal("0.5"))], baseline=baseline)
        assert out[0][1].accepted is True

    def test_zero_risk_marks_undefined_ratio(self) -> None:
        baseline = _metrics(risk="0.10", ret="0.10")
        candidate = _metrics(risk="0", ret="0.10")
        out = _opt().accept([("c1", candidate, Decimal("0.5"))], baseline=baseline)
        assert out[0][1].accepted is False
        assert out[0][1].reason == "risk_zero_undefined_ratio"
