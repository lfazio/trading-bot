"""Tests for ``trading_system.strategy_lab.scoring``.

REQ refs:
- REQ_F_MTO_003 — score weights pinned 0.4 / 0.3 / 0.2 / 0.1.
- REQ_SDD_CFG_005 — default meta-loop scoring weights SHALL be
  0.4 / 0.3 / 0.2 / 0.1 (asserted below via the
  ``score_metrics`` algebra: weights are not parameters — they
  live in the source as ``_W_RETURN`` / ``_W_SHARPE`` /
  ``_W_STABILITY`` / ``_W_DD_PENALTY`` module constants that
  match the documented defaults).
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.scoring import score_metrics


def _metrics(
    ret: str = "0.10",
    sharpe: str = "1.0",
    stability: str = "0.7",
    dd_penalty: str = "0.1",
) -> StrategyMetrics:
    return StrategyMetrics(
        net_after_tax_return=Decimal(ret),
        sharpe=Decimal(sharpe),
        stability=Decimal(stability),
        dd_penalty=Decimal(dd_penalty),
        max_drawdown=Decimal(dd_penalty),  # alias when caller doesn't override
        turnover=Decimal("0"),
        regime_stability=Decimal("0.5"),
        leverage=Decimal("1"),
        parameter_sensitivity=Decimal("0.5"),
        risk=Decimal("0.1"),
        return_=Decimal(ret),
    )


def test_canonical_weights() -> None:
    # 0.4 * 0.10 + 0.3 * 1.0 + 0.2 * 0.7 + 0.1 * 0.1
    # = 0.04 + 0.30 + 0.14 + 0.01 = 0.49
    assert score_metrics(_metrics()) == Decimal("0.49")


def test_zero_metrics_yields_zero_score() -> None:
    m = StrategyMetrics(
        net_after_tax_return=Decimal(0),
        sharpe=Decimal(0),
        stability=Decimal(0),
        dd_penalty=Decimal(0),
        max_drawdown=Decimal(0),
        turnover=Decimal(0),
        regime_stability=Decimal(0),
        leverage=Decimal(0),
        parameter_sensitivity=Decimal(0),
        risk=Decimal(0),
        return_=Decimal(0),
    )
    assert score_metrics(m) == Decimal(0)


def test_score_pure_function_no_state() -> None:
    # Same inputs -> same output, every time.
    m = _metrics(ret="0.05", sharpe="0.5", stability="0.4", dd_penalty="0.05")
    assert score_metrics(m) == score_metrics(m) == score_metrics(m)
