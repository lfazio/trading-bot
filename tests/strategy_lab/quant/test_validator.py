"""Tests for the 5-gate ``HypothesisValidator`` (REQ_F_QNT_004,
REQ_SDS_QNT_002, REQ_SDD_QNT_002).

Each gate is exercised in isolation by constructing a Hypothesis
that passes every prior gate but trips the targeted one. The
gates SHALL be checked in strict order with first-fail short-circuit
— ``test_first_fail_short_circuits`` pins the ordering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.result import Err, Ok
from trading_system.strategy_lab.quant.hypothesis import (
    DatasetWindow,
    Direction,
    Hypothesis,
    HypothesisId,
)
from trading_system.strategy_lab.quant.validator import (
    HypothesisValidator,
    ValidatorConfig,
)


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _validator(*, now: datetime = _NOW, **cfg_overrides: object) -> HypothesisValidator:
    return HypothesisValidator(
        cfg=ValidatorConfig(**cfg_overrides),
        now=lambda: now,
    )


def _window(months: int = 3, frequency: str = "1d") -> DatasetWindow:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    if frequency == "1d":
        end = datetime(2026, 1 + months, 1, tzinfo=UTC)
    else:
        end = datetime(2026, 1, 5, tzinfo=UTC)  # 4-day intraday window
    return DatasetWindow(start=start, end=end, frequency=frequency)


def _h(**overrides: object) -> Hypothesis:
    base: dict[str, object] = dict(
        id=HypothesisId("h-1"),
        claim="adjusted_sharpe of dividend aristocrats stays above 1.2",
        falsification_criterion="reject if adjusted_sharpe < 0.8",
        dataset_window=_window(),
        metric="adjusted_sharpe",
        expected_direction=Direction.POSITIVE,
        operator_rationale="quality composite reduces vol",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    base.update(overrides)
    return Hypothesis(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Gate 1: structural (placeholder text)
# ---------------------------------------------------------------------------


def test_gate_structural_passes_real_claim() -> None:
    assert isinstance(_validator().validate(_h()), Ok)


def test_gate_structural_rejects_placeholder_claim() -> None:
    h = _h(claim="TBD")
    match _validator().validate(h):
        case Err(reason):
            assert reason == "hypothesis:structural:claim"
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# Gate 2: bounds
# ---------------------------------------------------------------------------


def test_gate_bounds_rejects_implausible_sharpe() -> None:
    # Sharpe bounds default to [-3, 3]; claim asserts 5.5.
    h = _h(claim="adjusted_sharpe will be 5.5 every quarter")
    match _validator().validate(h):
        case Err(reason):
            assert reason == "hypothesis:bounds:adjusted_sharpe"
        case _:
            raise AssertionError("expected Err")


def test_gate_bounds_rejects_implausible_yield_percent() -> None:
    # net_after_tax_return bounds default to [-1, 10] (so anything
    # under 1000% net return is fine). Build a claim with 4000%.
    h = _h(
        metric="net_after_tax_return",
        claim="net_after_tax_return of dividend aristocrats will reach 4000% in 3 months",
    )
    match _validator().validate(h):
        case Err(reason):
            assert reason == "hypothesis:bounds:net_after_tax_return"
        case _:
            raise AssertionError("expected Err")


def test_gate_bounds_passes_silent_claim() -> None:
    # A claim with no numeric literal at all SHALL pass the bounds gate.
    h = _h(claim="adjusted_sharpe of dividend aristocrats is meaningful")
    assert isinstance(_validator().validate(h), Ok)


# ---------------------------------------------------------------------------
# Gate 3: falsifiable
# ---------------------------------------------------------------------------


def test_gate_falsifiable_rejects_vague_criterion() -> None:
    h = _h(falsification_criterion="strategy will be good on average")
    match _validator().validate(h):
        case Err(reason):
            assert reason == "hypothesis:not_falsifiable"
        case _:
            raise AssertionError("expected Err")


def test_gate_falsifiable_accepts_inequality_token() -> None:
    h = _h(falsification_criterion="reject if adjusted_sharpe drops below 0.5")
    assert isinstance(_validator().validate(h), Ok)


# ---------------------------------------------------------------------------
# Gate 4: metric alignment
# ---------------------------------------------------------------------------


def test_gate_metric_rejects_unknown_metric() -> None:
    h = _h(metric="cosmic_ray_intensity")
    match _validator().validate(h):
        case Err(reason):
            assert reason.startswith("hypothesis:metric_mismatch:unknown_metric:")
        case _:
            raise AssertionError("expected Err")


def test_gate_metric_rejects_claim_drift() -> None:
    # Metric is "sharpe" but the claim is entirely about turnover.
    # Numbers in the claim (2.5) stay within sharpe's bounds [-3, 3]
    # so gate 2 passes; gate 4 catches the drift.
    h = _h(
        metric="sharpe",
        claim="turnover stays around 2.5 per month",
        falsification_criterion="reject if turnover exceeds 2.0",
    )
    match _validator().validate(h):
        case Err(reason):
            assert reason == "hypothesis:metric_mismatch:claim_metric_drift"
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# Gate 5: dataset sanity
# ---------------------------------------------------------------------------


def test_gate_dataset_rejects_future_end_window() -> None:
    h = _h(
        dataset_window=DatasetWindow(
            start=datetime(2026, 5, 1, tzinfo=UTC),
            end=datetime(2027, 1, 1, tzinfo=UTC),  # > _NOW
            frequency="1d",
        )
    )
    match _validator().validate(h):
        case Err(reason):
            assert reason == "hypothesis:bad_window:future_end"
        case _:
            raise AssertionError("expected Err")


def test_gate_dataset_rejects_too_short_daily_window() -> None:
    h = _h(
        dataset_window=DatasetWindow(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 10, tzinfo=UTC),  # 9 days < 30
            frequency="1d",
        )
    )
    match _validator().validate(h):
        case Err(reason):
            assert reason == "hypothesis:bad_window:too_short_for_timescale"
        case _:
            raise AssertionError("expected Err")


def test_gate_dataset_accepts_short_intraday_window() -> None:
    h = _h(
        dataset_window=DatasetWindow(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 3, tzinfo=UTC),  # 2 days, intraday OK
            frequency="1h",
        ),
    )
    assert isinstance(_validator().validate(h), Ok)


# ---------------------------------------------------------------------------
# First-fail short-circuit (gate ordering)
# ---------------------------------------------------------------------------


def test_first_fail_short_circuits() -> None:
    """Gate 1 (structural) trips ⇒ gates 2-5 SHALL NOT run.

    Constructing a hypothesis that would fail every gate; assert the
    Err is the structural one (proves gates 2-5 weren't checked)."""
    h = _h(
        claim="TBD",  # gate 1 trips
        falsification_criterion="strategy is fine",  # gate 3 also would trip
        metric="cosmic_ray_intensity",  # gate 4 also would trip
        dataset_window=DatasetWindow(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2027, 1, 1, tzinfo=UTC),  # gate 5 also would trip
            frequency="1d",
        ),
    )
    match _validator().validate(h):
        case Err(reason):
            assert reason == "hypothesis:structural:claim"
        case _:
            raise AssertionError("expected Err")


def test_validator_determinism() -> None:
    """Two validate() calls on the same hypothesis SHALL return
    equal Results (REQ_NF_QNT_002)."""
    h = _h(claim="TBD")
    v = _validator()
    a = v.validate(h)
    b = v.validate(h)
    assert a == b


# ---------------------------------------------------------------------------
# Bounds — custom table
# ---------------------------------------------------------------------------


def test_custom_bounds_table_tightens_sharpe() -> None:
    cfg = ValidatorConfig(
        bounds_table={"adjusted_sharpe": (Decimal("0"), Decimal("1"))},
    )
    v = HypothesisValidator(cfg=cfg, now=lambda: _NOW)
    h = _h(claim="adjusted_sharpe of dividend aristocrats above 1.5")
    match v.validate(h):
        case Err(reason):
            assert reason == "hypothesis:bounds:adjusted_sharpe"
        case _:
            raise AssertionError("expected Err")
