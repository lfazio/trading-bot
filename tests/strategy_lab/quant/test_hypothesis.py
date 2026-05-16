"""Tests for ``Hypothesis`` / ``HypothesisResult`` shape invariants
(REQ_F_QNT_001, REQ_SDS_QNT_001, REQ_SDD_QNT_001)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.strategy_lab.quant.hypothesis import (
    DatasetWindow,
    Direction,
    Hypothesis,
    HypothesisId,
    HypothesisResult,
    HypothesisState,
)


def _window() -> DatasetWindow:
    return DatasetWindow(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 6, 1, tzinfo=UTC),
        frequency="1d",
    )


def _hypothesis(**overrides: object) -> Hypothesis:
    base = dict(
        id=HypothesisId("h-1"),
        claim="adjusted_sharpe of dividend aristocrats > 1.2",
        falsification_criterion="reject if adjusted_sharpe < 0.8",
        dataset_window=_window(),
        metric="adjusted_sharpe",
        expected_direction=Direction.POSITIVE,
        operator_rationale="quality + low vol composite should add risk-adjusted return",
        created_at=datetime(2026, 5, 16, tzinfo=UTC),
    )
    base.update(overrides)
    return Hypothesis(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# DatasetWindow invariants
# ---------------------------------------------------------------------------


def test_window_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="end must be > start"):
        DatasetWindow(
            start=datetime(2024, 6, 1, tzinfo=UTC),
            end=datetime(2024, 1, 1, tzinfo=UTC),
            frequency="1d",
        )


def test_window_rejects_equal_start_end() -> None:
    t = datetime(2024, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="end must be > start"):
        DatasetWindow(start=t, end=t, frequency="1d")


def test_window_rejects_empty_frequency() -> None:
    with pytest.raises(ValueError, match="frequency"):
        DatasetWindow(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 6, 1, tzinfo=UTC),
            frequency="   ",
        )


def test_window_duration_days() -> None:
    w = DatasetWindow(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 31, tzinfo=UTC),
        frequency="1d",
    )
    assert w.duration_days() == 30


# ---------------------------------------------------------------------------
# Hypothesis invariants
# ---------------------------------------------------------------------------


def test_hypothesis_happy_path() -> None:
    h = _hypothesis()
    assert h.state is HypothesisState.PENDING


def test_hypothesis_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="id"):
        _hypothesis(id=HypothesisId(""))


def test_hypothesis_rejects_empty_claim() -> None:
    with pytest.raises(ValueError, match="claim"):
        _hypothesis(claim="   ")


def test_hypothesis_rejects_empty_falsification_criterion() -> None:
    with pytest.raises(ValueError, match="falsification_criterion"):
        _hypothesis(falsification_criterion="")


def test_hypothesis_rejects_empty_metric() -> None:
    with pytest.raises(ValueError, match="metric"):
        _hypothesis(metric="")


def test_hypothesis_rejects_empty_operator_rationale() -> None:
    with pytest.raises(ValueError, match="operator_rationale"):
        _hypothesis(operator_rationale="")


def test_hypothesis_rejects_non_strenum_state() -> None:
    with pytest.raises(TypeError, match="state"):
        _hypothesis(state="pending")  # type: ignore[arg-type]


def test_hypothesis_is_frozen() -> None:
    h = _hypothesis()
    with pytest.raises(Exception):
        h.state = HypothesisState.VALIDATED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# HypothesisResult invariants
# ---------------------------------------------------------------------------


def test_result_validated_requires_empty_rejection_reason() -> None:
    with pytest.raises(ValueError, match="rejection_reason"):
        HypothesisResult(
            hypothesis_id=HypothesisId("h-1"),
            outcome=HypothesisState.VALIDATED,
            confidence_band=(Decimal("0"), Decimal("1")),
            decided_at=datetime(2026, 5, 16, tzinfo=UTC),
            rejection_reason="oops",
        )


def test_result_rejected_requires_non_empty_rejection_reason() -> None:
    with pytest.raises(ValueError, match="rejection_reason"):
        HypothesisResult(
            hypothesis_id=HypothesisId("h-1"),
            outcome=HypothesisState.REJECTED,
            confidence_band=(Decimal("0"), Decimal("1")),
            decided_at=datetime(2026, 5, 16, tzinfo=UTC),
            rejection_reason="   ",
        )


def test_result_pending_outcome_rejected() -> None:
    with pytest.raises(ValueError, match="VALIDATED or REJECTED"):
        HypothesisResult(
            hypothesis_id=HypothesisId("h-1"),
            outcome=HypothesisState.PENDING,
            confidence_band=(Decimal("0"), Decimal("1")),
            decided_at=datetime(2026, 5, 16, tzinfo=UTC),
        )


def test_result_band_lower_above_upper_rejected() -> None:
    with pytest.raises(ValueError, match="confidence_band"):
        HypothesisResult(
            hypothesis_id=HypothesisId("h-1"),
            outcome=HypothesisState.VALIDATED,
            confidence_band=(Decimal("1"), Decimal("0")),
            decided_at=datetime(2026, 5, 16, tzinfo=UTC),
        )
