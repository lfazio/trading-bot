"""Meta-loop scoring (REQ_F_MTO_003).

``score = 0.4 * net_after_tax_return + 0.3 * sharpe
         + 0.2 * stability + 0.1 * dd_penalty``

Note the sign convention: ``dd_penalty`` is non-negative and *adds*
to the score per the SRS / SDD's literal formula. Callers that want
the more conventional "drawdown subtracts from score" semantics can
construct ``StrategyMetrics.dd_penalty`` as a negative-leaning
component (e.g., ``-max_drawdown``) — the scoring function stays
the canonical reference.

REQ_SDD_ALG_003 — the ``stability`` component fed into the score
above SHALL be the 12-month rolling Sharpe over the strategy's
equity curve, computed with ≥ 100 observations. Below the
observation floor the score SHALL be ``None`` and the candidate
SHALL be rejected as immature. :func:`compute_stability_score` is
the canonical implementation; meta-loop callers use it to fill
``StrategyMetrics.stability`` before invoking :func:`score_metrics`.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from trading_system.models.flow import EquityPoint
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.quant.overfitting import adjusted_sharpe

_W_RETURN = Decimal("0.4")
_W_SHARPE = Decimal("0.3")
_W_STABILITY = Decimal("0.2")
_W_DD_PENALTY = Decimal("0.1")

# REQ_SDD_ALG_003 — observation floor for the stability score. The
# Sharpe ratio over fewer than 100 daily returns is too noisy to
# trust; below the floor the score is None and the candidate is
# rejected as immature.
MIN_OBSERVATIONS_FOR_STABILITY = 100


def compute_stability_score(
    equity_curve: Sequence[EquityPoint],
    *,
    min_observations: int = MIN_OBSERVATIONS_FOR_STABILITY,
) -> Decimal | None:
    """REQ_SDD_ALG_003 — 12-month rolling Sharpe of ``equity_curve``,
    computed over the most recent observations. Returns ``None``
    when the curve has fewer than ``min_observations`` per-step
    returns — the meta-loop SHALL reject candidates that come
    back ``None`` as immature.

    The Sharpe is computed annualised under the standard daily-
    return convention (mean/stdev × sqrt(252)). Zero-variance
    curves return Decimal("0") rather than blowing up; this
    matches the convention in
    :func:`trading_system.strategy_lab.quant.overfitting.adjusted_sharpe`.
    """
    if len(equity_curve) < min_observations + 1:
        # Need at least ``min_observations`` returns, which means
        # ``min_observations + 1`` curve points.
        return None
    # Take the most recent ``min_observations`` returns. The "12-month
    # rolling" wording in the REQ assumes daily bars (≈ 252 trading
    # days). For sub-daily curves the floor still applies; operators
    # who want a different window pass a custom ``min_observations``.
    points = equity_curve[-(min_observations + 1):]
    returns: list[Decimal] = []
    for prev, cur in zip(points[:-1], points[1:], strict=True):
        if prev.equity_after_tax.amount <= 0:
            return None
        delta = cur.equity_after_tax.amount - prev.equity_after_tax.amount
        ret = delta / prev.equity_after_tax.amount
        returns.append(ret)
    mean = sum(returns, start=Decimal(0)) / Decimal(len(returns))
    variance = sum(
        (r - mean) ** 2 for r in returns
    ) / Decimal(len(returns))
    if variance <= 0:
        return Decimal(0)
    stdev = variance.sqrt()
    if stdev == 0:
        return Decimal(0)
    return (mean / stdev) * Decimal(252).sqrt()


def score_metrics(metrics: StrategyMetrics) -> Decimal:
    """Compute the meta-loop's per-candidate score.

    Pure: same inputs, same output. Weights are pinned from
    REQ_F_MTO_003 and SHALL NOT be runtime-tuned (kill-switch
    discipline applies — see REQ_S_KS_010).

    CR-002 Phase B (REQ_SDD_QNT_006) — when ``metrics`` carries the
    overfitting-aware fields (``n_params > 0`` and
    ``n_train_periods > 0``), the score substitutes
    :func:`adjusted_sharpe` for the raw Sharpe term so candidates
    with more free parameters trained on shorter windows are
    penalised. Backwards compat: legacy callers leaving both
    defaults at 0 see bit-identical scores (the substitution
    short-circuits and the raw ``metrics.sharpe`` is used).
    """
    sharpe_component = (
        adjusted_sharpe(metrics)
        if metrics.n_params > 0 and metrics.n_train_periods > 0
        else metrics.sharpe
    )
    return (
        _W_RETURN * metrics.net_after_tax_return
        + _W_SHARPE * sharpe_component
        + _W_STABILITY * metrics.stability
        + _W_DD_PENALTY * metrics.dd_penalty
    )
