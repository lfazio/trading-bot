"""CR-002 Phase B — ``hypothesis_ids`` tuple on ``StrategyCandidate``
+ ``ImprovementReport`` (REQ_F_QNT_005) + ``adjusted_sharpe``-aware
scoring (REQ_SDD_QNT_006)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import StrategyId
from trading_system.models.meta import ImprovementReport
from trading_system.models.phase import AllocationBucket
from trading_system.strategies.protocol import Strategy
from trading_system.strategy_lab.candidate import StrategyCandidate
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.scoring import score_metrics


# ---------------------------------------------------------------------------
# StrategyCandidate.hypothesis_ids — REQ_F_QNT_005
# ---------------------------------------------------------------------------


class _NoopStrategy:
    id: StrategyId = StrategyId("noop")

    def evaluate(self, state):  # type: ignore[no-untyped-def]
        _ = state
        return []


def _candidate(
    *,
    cid: str = "cand-1",
    hypothesis_ids: tuple[str, ...] = (),
) -> StrategyCandidate:
    return StrategyCandidate(
        id=StrategyId(cid),
        strategy_factory=_NoopStrategy,
        bucket=AllocationBucket.STOCK,
        seed=1,
        config_hash=f"hash-{cid}",
        generated_at=datetime(2026, 5, 18, tzinfo=UTC),
        hypothesis_ids=hypothesis_ids,
    )


def test_candidate_accepts_empty_hypothesis_ids_by_default() -> None:
    """REQ_NF_QNT_001 / REQ_NF_ACC_001 mirror — legacy hypothesis-
    naive generators leave the tuple empty + everything still
    works (bit-identical to pre-Phase-B behaviour)."""
    c = _candidate()
    assert c.hypothesis_ids == ()


def test_candidate_accepts_sorted_unique_hypothesis_ids() -> None:
    c = _candidate(hypothesis_ids=("h-001", "h-002", "h-010"))
    assert c.hypothesis_ids == ("h-001", "h-002", "h-010")


def test_candidate_rejects_unsorted_hypothesis_ids() -> None:
    """REQ_NF_QNT_002 family — sorted lex for byte-identical replay."""
    with pytest.raises(ValueError, match="hypothesis_ids must be sorted"):
        _candidate(hypothesis_ids=("h-010", "h-001"))


def test_candidate_rejects_duplicate_hypothesis_ids() -> None:
    with pytest.raises(ValueError, match="hypothesis_ids must be unique"):
        _candidate(hypothesis_ids=("h-001", "h-001"))


def test_candidate_rejects_empty_hypothesis_id_entry() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _candidate(hypothesis_ids=("h-001", ""))


# ---------------------------------------------------------------------------
# ImprovementReport.hypothesis_ids — REQ_F_QNT_005
# ---------------------------------------------------------------------------


def _report(**overrides):  # type: ignore[no-untyped-def]
    defaults = dict(
        cycle_id="cycle-1",
        best_strategy_id=StrategyId("cand-1"),
        deltas={},
        risk_assessment="ok",
        rejected=(),
        rejection_reasons={},
        generated_at=datetime(2026, 5, 18, tzinfo=UTC),
    )
    defaults.update(overrides)
    return ImprovementReport(**defaults)


def test_report_accepts_empty_hypothesis_ids_default() -> None:
    r = _report()
    assert r.hypothesis_ids == ()


def test_report_accepts_sorted_hypothesis_ids() -> None:
    r = _report(hypothesis_ids=("h-001", "h-010"))
    assert r.hypothesis_ids == ("h-001", "h-010")


def test_report_rejects_unsorted_hypothesis_ids() -> None:
    with pytest.raises(ValueError, match="sorted"):
        _report(hypothesis_ids=("h-010", "h-001"))


def test_report_rejects_duplicate_hypothesis_ids() -> None:
    with pytest.raises(ValueError, match="unique"):
        _report(hypothesis_ids=("h-001", "h-001"))


def test_report_rejects_empty_hypothesis_id_entry() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _report(hypothesis_ids=("h-001", "   "))


# ---------------------------------------------------------------------------
# REQ_SDD_QNT_006 — score_metrics uses adjusted_sharpe when fields set
# ---------------------------------------------------------------------------


def _metrics(
    *,
    sharpe: str = "0.5",
    n_params: int = 0,
    n_train_periods: int = 0,
) -> StrategyMetrics:
    return StrategyMetrics(
        net_after_tax_return=Decimal("0.10"),
        sharpe=Decimal(sharpe),
        stability=Decimal("0.5"),
        dd_penalty=Decimal("0.05"),
        max_drawdown=Decimal("0.10"),
        turnover=Decimal("1.0"),
        regime_stability=Decimal("0.7"),
        leverage=Decimal("1.0"),
        parameter_sensitivity=Decimal("0.2"),
        risk=Decimal("0.15"),
        return_=Decimal("0.10"),
        n_params=n_params,
        n_train_periods=n_train_periods,
    )


def test_score_uses_raw_sharpe_when_overfitting_fields_default_zero() -> None:
    """Backwards compat — legacy callers leaving n_params=0 /
    n_train_periods=0 see the bit-identical pre-Phase-B score."""
    m = _metrics(sharpe="0.5")
    score = score_metrics(m)
    # 0.4 * 0.10 + 0.3 * 0.5 + 0.2 * 0.5 + 0.1 * 0.05
    # = 0.04 + 0.15 + 0.10 + 0.005 = 0.295
    assert score == Decimal("0.295")


def test_score_substitutes_adjusted_sharpe_when_overfitting_fields_set() -> None:
    """REQ_SDD_QNT_006 — penalises overfitted candidates."""
    m_overfit = _metrics(sharpe="0.5", n_params=10, n_train_periods=20)
    m_baseline = _metrics(sharpe="0.5", n_params=0, n_train_periods=0)
    overfit_score = score_metrics(m_overfit)
    baseline_score = score_metrics(m_baseline)
    # Overfitted candidate's score SHALL be strictly lower.
    assert overfit_score < baseline_score


def test_score_with_clean_overfitting_fields_close_to_raw() -> None:
    """Plenty of data + few params ⇒ adjusted_sharpe ≈ raw_sharpe."""
    m_clean = _metrics(sharpe="0.5", n_params=2, n_train_periods=10000)
    score_clean = score_metrics(m_clean)
    # Should be within 1% of the no-adjustment score.
    raw = score_metrics(_metrics(sharpe="0.5"))
    delta = abs(score_clean - raw)
    assert delta < Decimal("0.005"), f"clean score {score_clean} diverges too much from {raw}"
