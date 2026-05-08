"""``Optimizer`` — safe-self-improvement filter (REQ_F_MTO_006).

After the score-and-rank step, the optimizer admits at most
``top_k`` candidates and, for each, demands:

1. ``candidate.risk <= baseline.risk`` — risk MUST NOT increase, AND
2. ``candidate.return_ / candidate.risk > baseline.return_ / baseline.risk``
   — risk-adjusted return MUST strictly improve.

When the registry has no baseline (cold start), all top-k
candidates are accepted as the inaugural baseline.

The optimizer is the *only* gate authorised to mutate the registry's
"current best" pointer. The runtime never invokes it
(REQ_SDS_MOD_014); the loop controller does, and only as part of a
bounded research cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.strategy_lab.metrics import StrategyMetrics


@dataclass(frozen=True, slots=True)
class OptimizerConfig:
    """Cap on the per-cycle accept count."""

    top_k: int = 3

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError(f"OptimizerConfig.top_k must be > 0, got {self.top_k}")


@dataclass(frozen=True, slots=True)
class OptimizerDecision:
    """Per-candidate acceptance verdict."""

    accepted: bool
    reason: str  # "" when accepted; "risk_higher_than_baseline" / "return_per_risk_lower" / ...


@dataclass(slots=True)
class Optimizer:
    """Pure decision; carries configuration only."""

    cfg: OptimizerConfig

    def accept(
        self,
        ranked: list[tuple[str, StrategyMetrics, Decimal]],
        baseline: StrategyMetrics | None,
    ) -> list[tuple[str, OptimizerDecision]]:
        """Walk the ranked list (highest score first) and emit
        ``(candidate_id, decision)`` pairs.

        ``ranked`` carries ``(candidate_id, metrics, score)`` tuples;
        ranking is the caller's responsibility (the loop controller
        sorts before calling).

        Cold start (``baseline is None``): the top ``cfg.top_k``
        candidates are accepted unconditionally so the registry has a
        starting reference.

        Subsequent cycles: each candidate is evaluated against the
        baseline; the comparator is strict — equal-return /
        equal-risk does NOT improve the system (REQ_F_MTO_006).
        """
        out: list[tuple[str, OptimizerDecision]] = []
        for i, (cid, metrics, _score) in enumerate(ranked):
            if i >= self.cfg.top_k:
                out.append((cid, OptimizerDecision(accepted=False, reason="below_top_k")))
                continue
            decision = self._evaluate_candidate(metrics, baseline)
            out.append((cid, decision))
        return out

    @staticmethod
    def _evaluate_candidate(
        metrics: StrategyMetrics, baseline: StrategyMetrics | None
    ) -> OptimizerDecision:
        if baseline is None:
            return OptimizerDecision(accepted=True, reason="")
        if metrics.risk > baseline.risk:
            return OptimizerDecision(accepted=False, reason="risk_higher_than_baseline")
        if metrics.risk == 0 or baseline.risk == 0:
            # Without a meaningful risk denominator we can't enforce
            # the risk-adjusted comparator; refuse to upgrade.
            return OptimizerDecision(accepted=False, reason="risk_zero_undefined_ratio")
        candidate_ratio = metrics.return_ / metrics.risk
        baseline_ratio = baseline.return_ / baseline.risk
        if candidate_ratio <= baseline_ratio:
            return OptimizerDecision(accepted=False, reason="return_per_risk_not_improved")
        return OptimizerDecision(accepted=True, reason="")
