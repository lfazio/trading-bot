"""EU dividend / stock screener.

Filter + scoring pipeline that consumes ``data.fundamentals`` and
returns a ranked list of ``ScoredStock`` candidates for the strategy
layer.

REQ refs:
- REQ_F_SCR_001 — filter (yield 3-7 %, payout < 70 %, FCF > 0,
  D/E < 1.5, >= 5 y dividend history).
- REQ_F_SCR_002 — scored ranking with three components: stability,
  yield_quality, valuation.
- REQ_SDD_ALG_018 — evaluation order is yield -> payout -> FCF
  -> D/E -> history (cheapest first); observable in test traces.
- REQ_SDS_MOD_006 — strategy / engine modules see read-only input.
- REQ_SDS_ARC_002 — engine implemented as pure functions.
- REQ_SDD_IMP_006 — no module-level mutable state, no top-level I/O.

Score helpers (``stability_score`` / ``yield_quality_score`` /
``valuation_score``) use the fields actually present on
``Fundamentals``; the SDD pseudo-code hinted at richer inputs
(dividend-growth std-dev, P/FCF multiple) that the data type does not
yet carry. The concrete formulas are documented in ``engine.py`` and
are easy to swap once richer fundamentals land — the score Protocol
surface stays stable.
"""

from trading_system.screener.config import ScreenerConfig
from trading_system.screener.engine import (
    FILTER_RULES,
    FilterRule,
    ScoreBreakdown,
    ScoredStock,
    screen,
    stability_score,
    valuation_score,
    yield_quality_score,
)

__all__ = [
    "FILTER_RULES",
    "FilterRule",
    "ScoreBreakdown",
    "ScoredStock",
    "ScreenerConfig",
    "screen",
    "stability_score",
    "valuation_score",
    "yield_quality_score",
]
