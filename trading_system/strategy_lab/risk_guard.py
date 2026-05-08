"""``RiskGuard`` — hard pre-acceptance filter for candidate strategies.

A candidate SHALL pass the guard before its score is even compared
against the registry baseline. Each rejection reason is surfaced as
a categorised string so the ImprovementReport can document why
candidates were dropped (REQ_F_MTO_007).

Configuration is operator-supplied; defaults are the SRS / SDD
canonical values for a Phase-1 / Phase-2 deployment. Operators tune
``RiskGuardConfig`` as deployments mature into Phase 5+.

REQ refs: REQ_F_MTO_002 (step 3 hard gate), REQ_F_MTO_006 (safe
self-improvement; the guard is the necessary precondition the
optimizer assumes), REQ_F_MTO_008 (regime stability).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.strategy_lab.metrics import StrategyMetrics


@dataclass(frozen=True, slots=True)
class RiskGuardConfig:
    """Hard cutoffs applied to candidate metrics."""

    max_drawdown_cap: Decimal  # candidate.max_drawdown <= this
    turnover_max: Decimal  # candidate.turnover <= this
    regime_stability_min: Decimal  # candidate.regime_stability >= this
    leverage_cap: Decimal  # candidate.leverage <= this
    parameter_sensitivity_max: Decimal  # candidate.parameter_sensitivity <= this

    @classmethod
    def default_phase_1_2(cls) -> RiskGuardConfig:
        """Tighter Phase-1 / Phase-2 caps (15% DD, low leverage)."""
        return cls(
            max_drawdown_cap=Decimal("0.15"),
            turnover_max=Decimal("12"),  # ~1 trade/month for a Phase-1 cap
            regime_stability_min=Decimal("0.5"),
            leverage_cap=Decimal("1.0"),  # turbos disabled in Phase 1
            parameter_sensitivity_max=Decimal("0.5"),
        )


@dataclass(frozen=True, slots=True)
class RiskGuardVerdict:
    """Outcome of one ``RiskGuard.evaluate`` call."""

    passed: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.passed and self.reasons:
            raise ValueError(
                f"RiskGuardVerdict.passed=True must carry no reasons; got {self.reasons!r}"
            )
        if not self.passed and not self.reasons:
            raise ValueError("RiskGuardVerdict.passed=False must carry at least one reason")


@dataclass(frozen=True, slots=True)
class RiskGuard:
    """Pure check; configuration injected at construction."""

    cfg: RiskGuardConfig

    def evaluate(self, metrics: StrategyMetrics) -> RiskGuardVerdict:
        """Apply every cap; collect all violations so the report
        can show the full failure surface."""
        reasons: list[str] = []
        if metrics.max_drawdown > self.cfg.max_drawdown_cap:
            reasons.append(f"dd_breach:{metrics.max_drawdown}>{self.cfg.max_drawdown_cap}")
        if metrics.turnover > self.cfg.turnover_max:
            reasons.append(f"turnover_breach:{metrics.turnover}>{self.cfg.turnover_max}")
        if metrics.regime_stability < self.cfg.regime_stability_min:
            reasons.append(
                f"regime_unstable:{metrics.regime_stability}<{self.cfg.regime_stability_min}"
            )
        if metrics.leverage > self.cfg.leverage_cap:
            reasons.append(f"leverage_breach:{metrics.leverage}>{self.cfg.leverage_cap}")
        if metrics.parameter_sensitivity > self.cfg.parameter_sensitivity_max:
            reasons.append(
                f"sensitivity_breach:"
                f"{metrics.parameter_sensitivity}>{self.cfg.parameter_sensitivity_max}"
            )
        if reasons:
            return RiskGuardVerdict(passed=False, reasons=tuple(reasons))
        return RiskGuardVerdict(passed=True)

    def passes(self, metrics: StrategyMetrics) -> bool:
        """Boolean alias used by the loop controller."""
        return self.evaluate(metrics).passed
