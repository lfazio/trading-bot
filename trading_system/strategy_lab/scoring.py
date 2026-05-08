"""Meta-loop scoring (REQ_F_MTO_003).

``score = 0.4 * net_after_tax_return + 0.3 * sharpe
         + 0.2 * stability + 0.1 * dd_penalty``

Note the sign convention: ``dd_penalty`` is non-negative and *adds*
to the score per the SRS / SDD's literal formula. Callers that want
the more conventional "drawdown subtracts from score" semantics can
construct ``StrategyMetrics.dd_penalty`` as a negative-leaning
component (e.g., ``-max_drawdown``) — the scoring function stays
the canonical reference.
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.strategy_lab.metrics import StrategyMetrics

_W_RETURN = Decimal("0.4")
_W_SHARPE = Decimal("0.3")
_W_STABILITY = Decimal("0.2")
_W_DD_PENALTY = Decimal("0.1")


def score_metrics(metrics: StrategyMetrics) -> Decimal:
    """Compute the meta-loop's per-candidate score.

    Pure: same inputs, same output. Weights are pinned from
    REQ_F_MTO_003 and SHALL NOT be runtime-tuned (kill-switch
    discipline applies — see REQ_S_KS_010).
    """
    return (
        _W_RETURN * metrics.net_after_tax_return
        + _W_SHARPE * metrics.sharpe
        + _W_STABILITY * metrics.stability
        + _W_DD_PENALTY * metrics.dd_penalty
    )
