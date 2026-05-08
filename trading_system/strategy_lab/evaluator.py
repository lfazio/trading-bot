"""``Evaluator`` — turn a ``BacktestResult`` (+ optional walk-forward)
into a ``StrategyMetrics`` vector.

The evaluator is pure: same inputs, same metric vector. It does not
touch the data layer or the broker; everything it needs is on the
``BacktestResult`` produced by ``backtesting/`` and the ``WFResult``
produced by ``walk_forward``.

Where a metric is genuinely unobservable from a single backtest
(parameter sensitivity, regime stability), the evaluator emits a
sensible neutral value and documents the heuristic; the meta-loop
makes the choice explicit rather than inventing data.

REQ refs: REQ_F_MTO_002 (pipeline step 3 — compute metrics),
REQ_F_MTO_004 (walk-forward / OOS), REQ_F_MTO_007 (metrics consumed
by ImprovementReport), REQ_NF_REP_001.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.backtesting.result import BacktestResult
from trading_system.backtesting.walk_forward import WFResult, sharpe_ratio
from trading_system.capital_flow.flow import CapitalFlow
from trading_system.strategy_lab.metrics import StrategyMetrics

_NEUTRAL_PARAM_SENSITIVITY = Decimal("0.25")
_NEUTRAL_REGIME_STABILITY = Decimal("0.5")
_MIN_RETURNS = 2  # need at least two per-tick returns for a non-degenerate vol


@dataclass(slots=True)
class Evaluator:
    """Construct with optional defaults; calls are pure."""

    neutral_parameter_sensitivity: Decimal = _NEUTRAL_PARAM_SENSITIVITY
    neutral_regime_stability_when_no_wf: Decimal = _NEUTRAL_REGIME_STABILITY

    def compute(
        self,
        result: BacktestResult,
        capital_flow: CapitalFlow,
        wf: WFResult | None = None,
    ) -> StrategyMetrics:
        """Compute the canonical metric vector for a single run.

        ``capital_flow`` is needed so the canonical performance series
        (``equity_excl_injections``) is the source of truth for the
        ``net_after_tax_return`` field rather than the raw equity
        curve (REQ_SDS_MOD_005).

        ``wf`` is optional. When provided, the evaluator derives
        ``regime_stability`` from the spread of OOS Sharpe ratios
        across windows (lower spread = more stable). Without a
        walk-forward, the field is set to the neutral default.
        """
        return StrategyMetrics(
            net_after_tax_return=_total_return(result, capital_flow),
            sharpe=sharpe_ratio(result.equity_curve),
            stability=_stability_from_dd(result),
            dd_penalty=_dd_penalty(result),
            max_drawdown=_max_drawdown(result),
            turnover=Decimal(len(result.trades)),
            regime_stability=_regime_stability(wf, self.neutral_regime_stability_when_no_wf),
            leverage=Decimal(1),  # spot-only by default; turbo runs override
            parameter_sensitivity=self.neutral_parameter_sensitivity,
            risk=_realised_vol(result),
            return_=_total_return(result, capital_flow),
        )


# ----------------------------------------------------------------------
# Helpers — pure, no dependencies beyond stdlib + result types
# ----------------------------------------------------------------------


def _total_return(result: BacktestResult, capital_flow: CapitalFlow) -> Decimal:
    if not result.equity_excl_injections:
        return Decimal(0)
    initial = capital_flow.initial.amount
    if initial == 0:
        return Decimal(0)
    final = result.equity_excl_injections[-1]
    return (final - initial) / initial


def _max_drawdown(result: BacktestResult) -> Decimal:
    if not result.equity_curve:
        return Decimal(0)
    return max(p.drawdown_pct for p in result.equity_curve)


def _dd_penalty(result: BacktestResult) -> Decimal:
    """Drawdown penalty: large drawdown -> large penalty (in [0, 1]).

    Convention: penalty == max_drawdown. The scoring weights
    (REQ_F_MTO_003) treat ``dd_penalty`` as ADDITIVE so callers that
    want subtraction can pass ``-max_drawdown`` instead. We keep the
    canonical value here.
    """
    return _max_drawdown(result)


def _stability_from_dd(result: BacktestResult) -> Decimal:
    """Stability heuristic: 1 - max_drawdown, clamped to [0, 1].

    A run with no drawdown is perfectly stable; a -100% drawdown
    is maximally unstable. More sophisticated stability metrics
    (rolling-window correlation, regime-by-regime returns) ride
    on top of walk-forward and can replace this in a follow-up.
    """
    dd = _max_drawdown(result)
    out = Decimal(1) - dd
    if out < 0:
        return Decimal(0)
    if out > 1:
        return Decimal(1)
    return out


def _realised_vol(result: BacktestResult) -> Decimal:
    """Annualized vol of per-tick returns. Uses the same daily
    ``sqrt(252)`` factor as the Sharpe helper for consistency."""
    if len(result.equity_curve) < _MIN_RETURNS:
        return Decimal(0)
    returns: list[Decimal] = []
    for i in range(1, len(result.equity_curve)):
        prev = result.equity_curve[i - 1].equity_after_tax.amount
        cur = result.equity_curve[i].equity_after_tax.amount
        if prev == 0:
            continue
        returns.append((cur - prev) / prev)
    if len(returns) < _MIN_RETURNS:
        return Decimal(0)
    n = Decimal(len(returns))
    mean_r = sum(returns, start=Decimal(0)) / n
    var = sum(((r - mean_r) ** 2 for r in returns), start=Decimal(0)) / n
    if var == 0:
        return Decimal(0)
    return var.sqrt() * Decimal(252).sqrt()


def _regime_stability(wf: WFResult | None, neutral: Decimal) -> Decimal:
    """Derive regime stability from walk-forward OOS Sharpes.

    Heuristic: 1 - (std(oos_sharpes) / max(1, abs(mean(oos_sharpes)))),
    clamped to [0, 1]. Strategies with consistent OOS performance
    score near 1; those with wildly different OOS Sharpes per
    window score near 0.

    Without a walk-forward, returns the neutral default — so a
    candidate that hasn't been walk-forward-validated cannot get a
    free pass on regime stability simply by lacking the data.
    """
    if wf is None or not wf.windows:
        return neutral
    oos = [w.oos_sharpe for w in wf.windows]
    n = Decimal(len(oos))
    mean_s = sum(oos, start=Decimal(0)) / n
    var = sum(((s - mean_s) ** 2 for s in oos), start=Decimal(0)) / n
    if var == 0:
        return Decimal(1)
    std = var.sqrt()
    denom = abs(mean_s) if abs(mean_s) > Decimal(1) else Decimal(1)
    score = Decimal(1) - (std / denom)
    if score < 0:
        return Decimal(0)
    if score > 1:
        return Decimal(1)
    return score
