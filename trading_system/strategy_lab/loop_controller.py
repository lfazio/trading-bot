"""``LoopController`` — orchestrates the 8-step meta-loop pipeline.

Pipeline (REQ_F_MTO_002):

  1. generate      — Generator.propose(N).
  2. backtest      — LabBacktester.run per candidate.
  3. evaluate      — Evaluator.compute -> StrategyMetrics.
     RiskGuard hard gate filters here.
  4. walk-forward  — optional; if a wf_runner is supplied, OOS
     collapse rejects candidates.
  5. score         — score_metrics() ranks survivors.
  6. select        — Optimizer.accept against the registry's
     current baseline (REQ_F_MTO_006).
  7. registry      — accepted candidates land as RegistryEntry rows
     (validated=False by default; the operator promotes via
     Registry.mark_validated once a separate review approves).
  8. report        — emits an ImprovementReport for the cycle
     (REQ_F_MTO_007).

Per REQ_SDS_MOD_014 the runtime SHALL NOT import this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.backtesting.monte_carlo.errors import MonteCarloError
from trading_system.backtesting.monte_carlo.result import (
    QUINTILE_KEYS,
    MonteCarloResult,
)
from trading_system.backtesting.walk_forward import WFResult, detect_oos_collapse
from trading_system.models.identifiers import StrategyId
from trading_system.models.meta import ImprovementReport
from trading_system.result import Err, Nothing, Ok, Result, Some
from trading_system.strategy_lab.backtester import LabBacktester, LabBacktestResult
from trading_system.strategy_lab.candidate import StrategyCandidate
from trading_system.strategy_lab.evaluator import Evaluator
from trading_system.strategy_lab.generator import Generator
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.optimizer import Optimizer, OptimizerDecision
from trading_system.strategy_lab.registry import Registry, RegistryEntry
from trading_system.strategy_lab.risk_guard import RiskGuard
from trading_system.strategy_lab.scoring import score_metrics

WalkForwardRunner = Callable[[StrategyCandidate], WFResult]
MCRunStep = Callable[[StrategyCandidate], Result[MonteCarloResult, MonteCarloError]]


@dataclass(slots=True)
class LoopController:
    """Single-cycle controller. Call ``cycle()`` to run the pipeline."""

    generator: Generator
    backtester: LabBacktester
    evaluator: Evaluator
    risk_guard: RiskGuard
    optimizer: Optimizer
    registry: Registry
    candidates_per_cycle: int
    git_sha: str
    walk_forward_runner: WalkForwardRunner | None = None
    # CR-007 — optional Monte Carlo post-walk-forward gate. When set,
    # ``mc_run_step`` is invoked per surviving candidate; the runner's
    # 5th-percentile drawdown is compared against ``mc_drawdown_floor``
    # and candidates above the floor are rejected with
    # ``"mc:p5_drawdown_exceeds_phase_floor"``. None ⇒ MC step bypassed
    # entirely (REQ_F_MCS_005, TC_MCS_008).
    mc_run_step: MCRunStep | None = None
    mc_drawdown_floor: Decimal | None = None

    def cycle(self, *, cycle_id: str, at: datetime) -> ImprovementReport:
        """Run one full pipeline cycle and return its ImprovementReport."""
        # Step 1 — generate.
        candidates = self.generator.propose(self.candidates_per_cycle)
        rejected: dict[StrategyId, str] = {}

        # Step 2 — backtest.
        backtest_results: dict[StrategyId, LabBacktestResult] = {}
        for c in candidates:
            res = self.backtester.run(c)
            match res:
                case Ok(lr):
                    backtest_results[c.id] = lr
                case Err(reason):
                    rejected[c.id] = f"backtest_failed:{reason}"

        # Step 3 — evaluate + risk-guard filter.
        scored: list[tuple[StrategyCandidate, StrategyMetrics, Decimal]] = []
        for c in candidates:
            if c.id in rejected:
                continue
            lr = backtest_results[c.id]
            wf = self.walk_forward_runner(c) if self.walk_forward_runner else None
            # Step 4 — walk-forward / OOS collapse.
            if wf is not None and detect_oos_collapse(wf.windows):
                rejected[c.id] = "oos_collapse"
                continue
            # Step 4b — Monte Carlo (CR-007 REQ_F_MCS_005). When the
            # operator wires a runner, the candidate's 5th-percentile
            # drawdown SHALL stay at or below ``mc_drawdown_floor`` —
            # the phase's max-drawdown ceiling pulled from RiskConfig.
            if self.mc_run_step is not None:
                mc_outcome = self.mc_run_step(c)
                match mc_outcome:
                    case Err(mc_err):
                        rejected[c.id] = f"mc:{mc_err.category}"
                        continue
                    case Ok(mc_result):
                        if self.mc_drawdown_floor is not None:
                            p5_drawdown = mc_result.drawdown_percentiles[
                                QUINTILE_KEYS[0]  # 0.05
                            ]
                            if p5_drawdown > self.mc_drawdown_floor:
                                rejected[c.id] = "mc:p5_drawdown_exceeds_phase_floor"
                                continue
            metrics = self.evaluator.compute(lr.result, lr.capital_flow, wf=wf)
            verdict = self.risk_guard.evaluate(metrics)
            if not verdict.passed:
                rejected[c.id] = "risk_guard:" + ",".join(verdict.reasons)
                continue
            scored.append((c, metrics, score_metrics(metrics)))

        # Step 5 — rank highest-score-first.
        scored.sort(key=lambda x: -x[2])

        # Step 6 — optimizer accept against current baseline.
        baseline_metrics = _baseline_metrics(self.registry)
        ranked_for_optimizer = [(str(c.id), m, s) for c, m, s in scored]
        decisions = self.optimizer.accept(ranked_for_optimizer, baseline_metrics)
        decisions_by_id: dict[StrategyId, OptimizerDecision] = {
            StrategyId(cid): d for cid, d in decisions
        }

        # Step 7 — store accepted candidates.
        accepted_candidates: list[StrategyCandidate] = []
        for c, metrics, _score in scored:
            decision = decisions_by_id.get(c.id)
            if decision is None or not decision.accepted:
                if c.id not in rejected:
                    rejected[c.id] = "optimizer:" + (
                        decision.reason if decision is not None else "no_decision"
                    )
                continue
            entry = RegistryEntry(
                strategy_id=c.id,
                git_sha=self.git_sha,
                config_hash=c.config_hash,
                seed=c.seed,
                metrics=metrics,
                validated=False,  # operator promotes via Registry.mark_validated
                created_at=at,
                notes=f"cycle={cycle_id}",
            )
            store_res = self.registry.store(entry)
            if isinstance(store_res, Err):
                rejected[c.id] = f"registry_store_failed:{store_res.error}"
                continue
            accepted_candidates.append(c)

        # Step 8 — report.
        return _build_report(
            cycle_id=cycle_id,
            at=at,
            candidates=candidates,
            scored=scored,
            accepted=accepted_candidates,
            rejected=rejected,
            baseline_metrics=baseline_metrics,
        )


def _baseline_metrics(registry: Registry) -> StrategyMetrics | None:
    match registry.current():
        case Some(entry):
            return entry.metrics
        case Nothing():
            return None


def _build_report(  # noqa: PLR0913 — orchestration helper; flat shape matches caller
    *,
    cycle_id: str,
    at: datetime,
    candidates: tuple[StrategyCandidate, ...],
    scored: list[tuple[StrategyCandidate, StrategyMetrics, Decimal]],
    accepted: list[StrategyCandidate],
    rejected: dict[StrategyId, str],
    baseline_metrics: StrategyMetrics | None,
) -> ImprovementReport:
    """Assemble an ``ImprovementReport`` for the cycle (REQ_F_MTO_007).

    ``best_strategy_id`` is the highest-scoring accepted candidate;
    if none was accepted, it's None and ``rejected`` carries every
    candidate's rejection reason. ImprovementReport's invariant
    requires either an accepted best or at least one rejection — the
    cycle always produces one.
    """
    # Mark every non-accepted, non-rejected candidate as rejected
    # too — there are none in practice (the loop rejects explicitly
    # at every gate), but the ImprovementReport invariant is strict.
    for c in candidates:
        if c.id in rejected:
            continue
        if c not in accepted:
            rejected[c.id] = "not_accepted"

    best_id = accepted[0].id if accepted else None
    deltas = _build_deltas(scored, accepted, baseline_metrics)
    risk_assessment = _risk_assessment(scored, accepted, baseline_metrics)
    return ImprovementReport(
        cycle_id=cycle_id,
        best_strategy_id=best_id,
        deltas=deltas,
        risk_assessment=risk_assessment,
        rejected=tuple(rejected.keys()),
        rejection_reasons=dict(rejected),
        generated_at=at,
        notes=f"candidates={len(candidates)};accepted={len(accepted)}",
    )


def _build_deltas(
    scored: list[tuple[StrategyCandidate, StrategyMetrics, Decimal]],
    accepted: list[StrategyCandidate],
    baseline_metrics: StrategyMetrics | None,
) -> dict[str, Decimal]:
    """Compute deltas between the best accepted candidate and the
    registry baseline. Empty dict on cold start or no acceptance."""
    if not accepted:
        return {}
    by_id = {c.id: m for c, m, _ in scored}
    best = by_id.get(accepted[0].id)
    if best is None or baseline_metrics is None:
        return {}
    return {
        "return": best.net_after_tax_return - baseline_metrics.net_after_tax_return,
        "sharpe": best.sharpe - baseline_metrics.sharpe,
        "drawdown": best.max_drawdown - baseline_metrics.max_drawdown,
        "stability": best.stability - baseline_metrics.stability,
        "risk": best.risk - baseline_metrics.risk,
    }


def _risk_assessment(
    scored: list[tuple[StrategyCandidate, StrategyMetrics, Decimal]],
    accepted: list[StrategyCandidate],
    baseline_metrics: StrategyMetrics | None,
) -> str:
    """One-line summary of the cycle's risk posture."""
    if not accepted:
        return "no_acceptance"
    if baseline_metrics is None:
        return "cold_start_no_baseline"
    by_id = {c.id: m for c, m, _ in scored}
    best = by_id.get(accepted[0].id)
    if best is None:
        return "no_acceptance"
    cand_ratio = best.return_ / best.risk if best.risk else Decimal(0)
    base_ratio = (
        baseline_metrics.return_ / baseline_metrics.risk if baseline_metrics.risk else Decimal(0)
    )
    return (
        f"risk_delta={best.risk - baseline_metrics.risk};"
        f"return_per_risk_delta={cand_ratio}-{base_ratio}"
    )
