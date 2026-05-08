"""Bounded meta-optimization research engine.

Pipeline (REQ_F_MTO_002): generate -> backtest -> evaluate -> risk
guard -> walk-forward / OOS check -> score -> select -> registry ->
report.

Per REQ_SDS_MOD_014 the runtime SHALL import only
``trading_system.strategy_lab.registry`` (read-only). The other
sub-modules — generator, optimizer, backtester wrapper, loop
controller — are operator-run / out-of-band research tools and
SHALL NOT be imported by live code.

REQ refs: REQ_F_MTO_001..008, REQ_SDS_MOD_014, REQ_C_CLA_001,
REQ_C_CLA_002.
"""

from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.registry import Registry, RegistryEntry
from trading_system.strategy_lab.scoring import score_metrics

__all__ = [
    "Registry",
    "RegistryEntry",
    "StrategyMetrics",
    "score_metrics",
]
