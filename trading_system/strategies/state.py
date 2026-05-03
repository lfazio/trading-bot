"""``MarketState`` — frozen snapshot passed to ``Strategy.evaluate``.

REQ refs:
- REQ_SDS_MOD_006 — strategies see read-only state.
- REQ_SDD_API_001 — ``evaluate`` SHALL NOT mutate any field.
- REQ_SDD_TYP_001 — Decimal-backed money throughout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_system.data.provider import MarketDataProvider
from trading_system.models.phase import MarketRegime, PhaseConstraints
from trading_system.screener.engine import ScoredStock
from trading_system.strategies.protocol import PortfolioView


@dataclass(frozen=True, slots=True)
class MarketState:
    """All inputs a strategy needs at decision time.

    ``screener_ranking`` is a tuple (immutable) so a strategy cannot
    accidentally reorder it. ``market`` is the read-only data
    Protocol; concrete providers are deterministic for testing
    (REQ_F_BCT_001 / REQ_NF_DET_001).
    """

    at: datetime
    portfolio: PortfolioView
    constraints: PhaseConstraints
    regime: MarketRegime
    screener_ranking: tuple[ScoredStock, ...]
    market: MarketDataProvider
