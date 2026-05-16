"""Tests for ``HypothesisRunner`` orchestration (REQ_F_QNT_003/004/006,
REQ_SDS_QNT_002, REQ_SDD_QNT_004)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.result import Err, Ok, Result
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.quant.hypothesis import (
    DatasetWindow,
    Direction,
    Hypothesis,
    HypothesisId,
    HypothesisResult,
    HypothesisState,
)
from trading_system.strategy_lab.quant.library import (
    HypothesisLibrary,
    InMemoryHypothesisStore,
)
from trading_system.strategy_lab.quant.runner import (
    BacktesterAdapter,
    DefaultEvaluator,
    EvaluatorAdapter,
    HypothesisRunner,
)
from trading_system.strategy_lab.quant.validator import (
    HypothesisValidator,
    ValidatorConfig,
)


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _h() -> Hypothesis:
    return Hypothesis(
        id=HypothesisId("h-1"),
        claim="adjusted_sharpe of dividend aristocrats stays above 1.2",
        falsification_criterion="reject if adjusted_sharpe < 0.8",
        dataset_window=DatasetWindow(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 6, 1, tzinfo=UTC),
            frequency="1d",
        ),
        metric="adjusted_sharpe",
        expected_direction=Direction.POSITIVE,
        operator_rationale="quality composite",
        created_at=datetime(2026, 5, 16, tzinfo=UTC),
    )


def _good_metrics() -> StrategyMetrics:
    return StrategyMetrics(
        net_after_tax_return=Decimal("0.15"),
        sharpe=Decimal("1.5"),
        stability=Decimal("0.8"),
        dd_penalty=Decimal("0.2"),
        max_drawdown=Decimal("0.10"),
        turnover=Decimal("3.5"),
        regime_stability=Decimal("0.9"),
        leverage=Decimal("1.0"),
        parameter_sensitivity=Decimal("0.3"),
        risk=Decimal("0.12"),
        return_=Decimal("0.15"),
        n_params=5,
        n_train_periods=500,  # ratio = 0.01
        information_coefficient=Decimal("0.5"),  # > 0.30 floor
    )


@dataclass(slots=True)
class _FakeBacktester:
    """BacktesterAdapter test double: returns ``metrics`` for every
    call so the runner step-2 path is deterministic."""

    metrics: StrategyMetrics
    err: str | None = None
    calls: list[tuple[Hypothesis, int]] = field(default_factory=list)

    def run(
        self, hypothesis: Hypothesis, *, seed: int
    ) -> Result[StrategyMetrics, str]:
        self.calls.append((hypothesis, seed))
        if self.err is not None:
            return Err(self.err)
        return Ok(self.metrics)


@dataclass(slots=True)
class _FakeEvaluator:
    """EvaluatorAdapter test double — returns the configured outcome."""

    outcome: Result[HypothesisState, str]

    def decide(
        self, metrics: StrategyMetrics, hypothesis: Hypothesis
    ) -> Result[HypothesisState, str]:
        return self.outcome


def _build_runner(
    *,
    backtester: BacktesterAdapter,
    evaluator: EvaluatorAdapter,
    library: HypothesisLibrary | None = None,
) -> tuple[HypothesisRunner, HypothesisLibrary]:
    lib = library or HypothesisLibrary(store=InMemoryHypothesisStore())
    runner = HypothesisRunner(
        validator=HypothesisValidator(cfg=ValidatorConfig(), now=lambda: _NOW),
        backtester=backtester,
        evaluator=evaluator,
        library=lib,
        now=lambda: _NOW,
    )
    return runner, lib


# ---------------------------------------------------------------------------
# Adapter Protocol conformance
# ---------------------------------------------------------------------------


def test_fake_backtester_satisfies_protocol() -> None:
    assert isinstance(
        _FakeBacktester(metrics=_good_metrics()), BacktesterAdapter
    )


def test_fake_evaluator_satisfies_protocol() -> None:
    assert isinstance(
        _FakeEvaluator(outcome=Ok(HypothesisState.VALIDATED)),
        EvaluatorAdapter,
    )


# ---------------------------------------------------------------------------
# Validator-rejected path
# ---------------------------------------------------------------------------


def test_validator_rejection_short_circuits_before_backtester() -> None:
    """REQ_SDS_QNT_002 — validator rejection skips backtester+evaluator."""
    backtester = _FakeBacktester(metrics=_good_metrics())
    evaluator = _FakeEvaluator(outcome=Ok(HypothesisState.VALIDATED))
    runner, lib = _build_runner(backtester=backtester, evaluator=evaluator)

    # Build a hypothesis that trips gate 1 (TBD claim).
    h = Hypothesis(
        id=HypothesisId("h-bad"),
        claim="TBD",
        falsification_criterion="reject if metric < 0",
        dataset_window=DatasetWindow(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 6, 1, tzinfo=UTC),
            frequency="1d",
        ),
        metric="sharpe",
        expected_direction=Direction.POSITIVE,
        operator_rationale="placeholder draft",
        created_at=datetime(2026, 5, 16, tzinfo=UTC),
    )
    res = runner.run(h, seed=42).unwrap()
    assert isinstance(res, HypothesisResult)
    assert res.outcome is HypothesisState.REJECTED
    assert res.rejection_reason == "hypothesis:structural:claim"
    assert backtester.calls == []  # backtester NOT invoked


# ---------------------------------------------------------------------------
# Happy path (validator passes + backtester succeeds + evaluator validates)
# ---------------------------------------------------------------------------


def test_happy_path_validates_hypothesis() -> None:
    backtester = _FakeBacktester(metrics=_good_metrics())
    evaluator = _FakeEvaluator(outcome=Ok(HypothesisState.VALIDATED))
    runner, lib = _build_runner(backtester=backtester, evaluator=evaluator)
    lib.store_pending(_h()).unwrap()

    res = runner.run(_h(), seed=42).unwrap()
    assert res.outcome is HypothesisState.VALIDATED
    assert res.rejection_reason == ""
    assert len(backtester.calls) == 1

    # Audit row recorded.
    rows = lib.transitions_for(HypothesisId("h-1")).unwrap()
    assert len(rows) == 1
    assert rows[0].new_state is HypothesisState.VALIDATED


def test_runner_threads_seed_into_backtester() -> None:
    """REQ_NF_QNT_002 — deterministic by seed."""
    backtester = _FakeBacktester(metrics=_good_metrics())
    evaluator = _FakeEvaluator(outcome=Ok(HypothesisState.VALIDATED))
    runner, lib = _build_runner(backtester=backtester, evaluator=evaluator)
    lib.store_pending(_h()).unwrap()

    runner.run(_h(), seed=7).unwrap()
    assert backtester.calls[0][1] == 7


# ---------------------------------------------------------------------------
# Backtester-Err path
# ---------------------------------------------------------------------------


def test_backtester_error_rejects_with_prefix() -> None:
    backtester = _FakeBacktester(metrics=_good_metrics(), err="data:not_supported")
    evaluator = _FakeEvaluator(outcome=Ok(HypothesisState.VALIDATED))
    runner, lib = _build_runner(backtester=backtester, evaluator=evaluator)
    lib.store_pending(_h()).unwrap()

    res = runner.run(_h(), seed=42).unwrap()
    assert res.outcome is HypothesisState.REJECTED
    assert res.rejection_reason.startswith("backtester:")


# ---------------------------------------------------------------------------
# Evaluator-rejection paths
# ---------------------------------------------------------------------------


def test_evaluator_err_rejects() -> None:
    backtester = _FakeBacktester(metrics=_good_metrics())
    evaluator = _FakeEvaluator(
        outcome=Err("overfitting:parameter_to_data_ratio:0.10")
    )
    runner, lib = _build_runner(backtester=backtester, evaluator=evaluator)
    lib.store_pending(_h()).unwrap()

    res = runner.run(_h(), seed=42).unwrap()
    assert res.outcome is HypothesisState.REJECTED
    assert res.rejection_reason == "overfitting:parameter_to_data_ratio:0.10"


def test_evaluator_returns_pending_rejected() -> None:
    """An evaluator that returns ``Ok(PENDING)`` is a programmer
    error — the runner SHALL reject with a categorised reason."""
    backtester = _FakeBacktester(metrics=_good_metrics())
    evaluator = _FakeEvaluator(outcome=Ok(HypothesisState.PENDING))
    runner, lib = _build_runner(backtester=backtester, evaluator=evaluator)
    lib.store_pending(_h()).unwrap()

    res = runner.run(_h(), seed=42).unwrap()
    assert res.outcome is HypothesisState.REJECTED
    assert res.rejection_reason.startswith("hypothesis:evaluator_non_validated:")


# ---------------------------------------------------------------------------
# DefaultEvaluator — REQ_F_QNT_006 wiring
# ---------------------------------------------------------------------------


def test_default_evaluator_rejects_high_ratio() -> None:
    h = _h()
    bad = StrategyMetrics(
        net_after_tax_return=Decimal("0.15"),
        sharpe=Decimal("1.5"),
        stability=Decimal("0.8"),
        dd_penalty=Decimal("0.2"),
        max_drawdown=Decimal("0.10"),
        turnover=Decimal("3.5"),
        regime_stability=Decimal("0.9"),
        leverage=Decimal("1.0"),
        parameter_sensitivity=Decimal("0.3"),
        risk=Decimal("0.12"),
        return_=Decimal("0.15"),
        n_params=50,  # ratio = 0.5
        n_train_periods=100,
        information_coefficient=Decimal("0.5"),
    )
    match DefaultEvaluator().decide(bad, h):
        case Err(reason):
            assert reason.startswith("overfitting:parameter_to_data_ratio:")
        case _:
            raise AssertionError("expected Err")


def test_default_evaluator_rejects_direction_violation() -> None:
    """Expected POSITIVE but the metric value is non-positive ⇒
    direction violated."""
    h = _h()  # expected_direction = POSITIVE, metric = adjusted_sharpe
    neg = StrategyMetrics(
        net_after_tax_return=Decimal("0.15"),
        sharpe=Decimal("-0.5"),  # adjusted_sharpe will inherit
        stability=Decimal("0.8"),
        dd_penalty=Decimal("0.2"),
        max_drawdown=Decimal("0.10"),
        turnover=Decimal("3.5"),
        regime_stability=Decimal("0.9"),
        leverage=Decimal("1.0"),
        parameter_sensitivity=Decimal("0.3"),
        risk=Decimal("0.12"),
        return_=Decimal("0.15"),
        n_params=5,
        n_train_periods=500,
        information_coefficient=Decimal("0.5"),
    )
    match DefaultEvaluator().decide(neg, h):
        case Err(reason):
            assert reason.startswith("hypothesis:direction_violated:")
        case _:
            raise AssertionError("expected Err")


def test_default_evaluator_accepts_within_thresholds() -> None:
    h = _h()
    assert isinstance(DefaultEvaluator().decide(_good_metrics(), h), Ok)
