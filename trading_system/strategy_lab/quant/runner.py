"""``HypothesisRunner`` — orchestrates validator → backtester →
evaluator → library transition.

The backtester + evaluator are injected as Protocol-conforming
adapters so the runner stays decoupled from the concrete
``strategy_lab.backtester`` / ``strategy_lab.evaluator``. The
Phase-B sub-CR wires the existing types into the adapter slots.

REQ refs: REQ_F_QNT_003, REQ_F_QNT_004, REQ_F_QNT_006,
REQ_SDS_QNT_002, REQ_SDD_QNT_004.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.result import Err, Ok, Result
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.quant.hypothesis import (
    Hypothesis,
    HypothesisResult,
    HypothesisState,
)
from trading_system.strategy_lab.quant.library import HypothesisLibrary
from trading_system.strategy_lab.quant.overfitting import (
    overfitting_gate,
)
from trading_system.strategy_lab.quant.validator import HypothesisValidator


@runtime_checkable
class BacktesterAdapter(Protocol):
    """Adapter surface the runner expects from a backtester.

    Phase-B wires the existing ``strategy_lab.backtester.LabBacktester``
    by adding a thin wrapper that satisfies this Protocol.
    """

    def run(self, hypothesis: Hypothesis, *, seed: int) -> Result[StrategyMetrics, str]: ...


@runtime_checkable
class EvaluatorAdapter(Protocol):
    """Adapter surface for the post-backtest classifier.

    The default in v1 just consumes the overfitting gate from
    ``overfitting.py``; Phase-B may swap in a richer evaluator
    that combines REQ_F_QNT_006 with the existing
    ``strategy_lab.evaluator.Evaluator`` regime-stability /
    walk-forward checks.
    """

    def decide(
        self, metrics: StrategyMetrics, hypothesis: Hypothesis
    ) -> Result[HypothesisState, str]: ...


@dataclass(slots=True)
class DefaultEvaluator:
    """Reference adapter: runs the overfitting gate only.

    Returns ``Ok(HypothesisState.VALIDATED)`` when the gate passes
    AND the metric's expected direction is preserved (positive
    expected ⇒ metric value > 0; negative expected ⇒ < 0;
    two-tailed ⇒ always passes the direction check).
    """

    ratio_max: Decimal = Decimal("0.10")
    ic_floor: Decimal = Decimal("0.30")

    def decide(
        self, metrics: StrategyMetrics, hypothesis: Hypothesis
    ) -> Result[HypothesisState, str]:
        gate = overfitting_gate(
            metrics, ratio_max=self.ratio_max, ic_floor=self.ic_floor
        )
        match gate:
            case Err(reason):
                return Err(reason)
            case Ok(_):
                pass
        # Direction check on the named metric.
        value = _metric_value(metrics, hypothesis.metric)
        if value is None:
            return Err(f"hypothesis:metric_mismatch:unknown_metric:{hypothesis.metric}")
        from trading_system.strategy_lab.quant.hypothesis import Direction

        if hypothesis.expected_direction is Direction.POSITIVE and value <= 0:
            return Err(f"hypothesis:direction_violated:expected>0;got={value}")
        if hypothesis.expected_direction is Direction.NEGATIVE and value >= 0:
            return Err(f"hypothesis:direction_violated:expected<0;got={value}")
        # TWO_TAILED passes through.
        return Ok(HypothesisState.VALIDATED)


def _metric_value(metrics: StrategyMetrics, name: str) -> Decimal | None:
    """Read the named metric from the StrategyMetrics row.

    Returns ``None`` when ``name`` is outside the closed set the
    decider knows about — gate 4 in the validator should already
    have caught that, so this branch is defensive only.
    """
    return {
        "sharpe": metrics.sharpe,
        "adjusted_sharpe": metrics.sharpe,  # adjusted_sharpe computed elsewhere
        "net_after_tax_return": metrics.net_after_tax_return,
        "max_drawdown": metrics.max_drawdown,
        "information_coefficient": metrics.information_coefficient,
        "stability": metrics.stability,
        "turnover": metrics.turnover,
    }.get(name)


@dataclass(slots=True)
class HypothesisRunner:
    """Top-level orchestrator.

    Cycle:
        1. validator.validate(h) — first-fail short-circuits.
        2. backtester.run(h, seed=...) — deterministic Python sim.
        3. evaluator.decide(metrics, h) — classifies the outcome.
        4. library.transition(...) — records the audit trail.
        5. return HypothesisResult.
    """

    validator: HypothesisValidator
    backtester: BacktesterAdapter
    evaluator: EvaluatorAdapter
    library: HypothesisLibrary
    now: Callable[[], datetime] = field(default_factory=lambda: _default_now)

    def run(
        self, hypothesis: Hypothesis, *, seed: int
    ) -> Result[HypothesisResult, str]:
        # Step 1: validator.
        match self.validator.validate(hypothesis):
            case Err(reason):
                return self._reject(hypothesis, reason)
            case Ok(_):
                pass
        # Step 2: backtester.
        match self.backtester.run(hypothesis, seed=seed):
            case Err(reason):
                return self._reject(hypothesis, f"backtester:{reason}")
            case Ok(metrics):
                pass
        # Step 3: evaluator.
        match self.evaluator.decide(metrics, hypothesis):
            case Err(reason):
                return self._reject(hypothesis, reason)
            case Ok(state):
                if state is not HypothesisState.VALIDATED:
                    return self._reject(
                        hypothesis,
                        f"hypothesis:evaluator_non_validated:{state}",
                    )
        # Step 4: validated path.
        at = self.now()
        transition = self.library.transition(
            hypothesis.id,
            HypothesisState.VALIDATED,
            f"backtest passed; metric={hypothesis.metric}={metrics.sharpe}",
            at=at,
        )
        match transition:
            case Err(reason):
                return Err(reason)
            case Ok(_):
                pass
        return Ok(
            HypothesisResult(
                hypothesis_id=hypothesis.id,
                outcome=HypothesisState.VALIDATED,
                confidence_band=_metric_band(metrics, hypothesis.metric),
                decided_at=at,
                rejection_reason="",
            )
        )

    def _reject(
        self, hypothesis: Hypothesis, reason: str
    ) -> Result[HypothesisResult, str]:
        at = self.now()
        # If the hypothesis hasn't been stored yet (validator-rejected
        # before persistence), skip the library transition; otherwise
        # record the audit row.
        match self.library.get(hypothesis.id):
            case Err(reason_str):
                return Err(reason_str)
            case Ok(opt):
                if opt.is_some():
                    transition = self.library.transition(
                        hypothesis.id,
                        HypothesisState.REJECTED,
                        reason,
                        at=at,
                    )
                    match transition:
                        case Err(t_reason):
                            return Err(t_reason)
                        case Ok(_):
                            pass
        return Ok(
            HypothesisResult(
                hypothesis_id=hypothesis.id,
                outcome=HypothesisState.REJECTED,
                confidence_band=(Decimal(0), Decimal(0)),
                decided_at=at,
                rejection_reason=reason,
            )
        )


def _metric_band(metrics: StrategyMetrics, name: str) -> tuple[Decimal, Decimal]:
    """Return a ±10% band around the named metric value as the v1
    confidence-band placeholder. Phase B replaces this with a proper
    bootstrap CI computed by the backtester."""
    v = _metric_value(metrics, name)
    if v is None:
        return (Decimal(0), Decimal(0))
    band = abs(v) * Decimal("0.10")
    return (v - band, v + band)


def _default_now() -> datetime:
    return datetime.now(tz=UTC)
