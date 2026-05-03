"""Strategy layer (L4 decision).

Strategies consume read-only market / portfolio state and emit
``TradeProposal`` lists. They never execute trades — proposals flow
through the tax gate, risk engine, and kill switch before reaching
the broker (REQ_SDS_FLO_001).

Modules:
- ``state``    — ``MarketState`` carrying ``at``, portfolio view,
                 constraints, regime, screener ranking, market data
                 (REQ_SDS_MOD_006, REQ_SDD_API_001 — read-only).
- ``protocol`` — ``Strategy`` and ``PortfolioView`` Protocols
                 (REQ_F_STR_001..004, REQ_SDD_API_005, REQ_SDD_API_002).
- ``core``     — ``CoreStrategy`` long-term-holding rebalancer
                 (REQ_F_STR_001).
- ``tactical`` — ``TacticalStrategy`` trend / breakout / pullback
                 signal generator (REQ_F_STR_002).
- ``ensemble`` — ``EnsembleStrategy`` Phase-6 risk-parity wrapper
                 (REQ_F_STR_004, REQ_SDD_ALG_010).

REQ_F_STR_003 (every shipped strategy carries a walk-forward
certificate) is a Phase 6 / strategy_lab concern; here each strategy
exposes a stable ``id`` so the registry can attach a certificate.
"""

from trading_system.strategies.core import CoreStrategy, CoreStrategyConfig
from trading_system.strategies.ensemble import EnsembleMember, EnsembleStrategy
from trading_system.strategies.protocol import PortfolioView, Strategy
from trading_system.strategies.state import MarketState
from trading_system.strategies.tactical import (
    TacticalSignal,
    TacticalStrategy,
    TacticalStrategyConfig,
)

__all__ = [
    "CoreStrategy",
    "CoreStrategyConfig",
    "EnsembleMember",
    "EnsembleStrategy",
    "MarketState",
    "PortfolioView",
    "Strategy",
    "TacticalSignal",
    "TacticalStrategy",
    "TacticalStrategyConfig",
]
