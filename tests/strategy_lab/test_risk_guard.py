"""Tests for ``trading_system.strategy_lab.risk_guard``.

REQ refs: REQ_F_MTO_002 (hard gate), REQ_F_MTO_006, REQ_F_MTO_008.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.risk_guard import (
    RiskGuard,
    RiskGuardConfig,
    RiskGuardVerdict,
)


def _metrics(**overrides) -> StrategyMetrics:
    base = dict(
        net_after_tax_return=Decimal("0.10"),
        sharpe=Decimal("1.5"),
        stability=Decimal("0.7"),
        dd_penalty=Decimal("0.1"),
        max_drawdown=Decimal("0.1"),
        turnover=Decimal("8"),
        regime_stability=Decimal("0.6"),
        leverage=Decimal("1"),
        parameter_sensitivity=Decimal("0.2"),
        risk=Decimal("0.15"),
        return_=Decimal("0.10"),
    )
    base.update(overrides)
    return StrategyMetrics(**base)


def _guard() -> RiskGuard:
    return RiskGuard(cfg=RiskGuardConfig.default_phase_1_2())


class TestRiskGuard:
    def test_passes_metrics_within_caps(self) -> None:
        verdict = _guard().evaluate(_metrics())
        assert verdict.passed is True
        assert verdict.reasons == ()

    def test_rejects_dd_breach(self) -> None:
        verdict = _guard().evaluate(_metrics(max_drawdown=Decimal("0.20")))
        assert verdict.passed is False
        assert any("dd_breach" in r for r in verdict.reasons)

    def test_rejects_turnover_breach(self) -> None:
        verdict = _guard().evaluate(_metrics(turnover=Decimal("100")))
        assert any("turnover_breach" in r for r in verdict.reasons)

    def test_rejects_regime_unstable(self) -> None:
        verdict = _guard().evaluate(_metrics(regime_stability=Decimal("0.1")))
        assert any("regime_unstable" in r for r in verdict.reasons)

    def test_rejects_leverage_breach(self) -> None:
        verdict = _guard().evaluate(_metrics(leverage=Decimal("2")))
        assert any("leverage_breach" in r for r in verdict.reasons)

    def test_rejects_sensitivity_breach(self) -> None:
        verdict = _guard().evaluate(_metrics(parameter_sensitivity=Decimal("0.9")))
        assert any("sensitivity_breach" in r for r in verdict.reasons)

    def test_collects_all_reasons(self) -> None:
        verdict = _guard().evaluate(
            _metrics(
                max_drawdown=Decimal("0.20"),
                turnover=Decimal("100"),
                leverage=Decimal("2"),
            )
        )
        assert len(verdict.reasons) == 3

    def test_passes_alias_consistent(self) -> None:
        guard = _guard()
        m = _metrics()
        assert guard.passes(m) == guard.evaluate(m).passed


class TestRiskGuardVerdict:
    def test_passed_with_reasons_rejected(self) -> None:
        with pytest.raises(ValueError, match="must carry no reasons"):
            RiskGuardVerdict(passed=True, reasons=("anything",))

    def test_failed_without_reasons_rejected(self) -> None:
        with pytest.raises(ValueError, match="must carry at least one reason"):
            RiskGuardVerdict(passed=False)
