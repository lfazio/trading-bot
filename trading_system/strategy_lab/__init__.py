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

from trading_system.strategy_lab.backtester import LabBacktester, LabBacktestResult
from trading_system.strategy_lab.candidate import StrategyCandidate
from trading_system.strategy_lab.evaluator import Evaluator
from trading_system.strategy_lab.generator import Generator, StaticGenerator
from trading_system.strategy_lab.loop_controller import LoopController
from trading_system.strategy_lab.mc_drawdown_floor import MCDrawdownFloor
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.optimizer import (
    Optimizer,
    OptimizerConfig,
    OptimizerDecision,
)
from trading_system.strategy_lab.registry import Registry, RegistryEntry
from trading_system.strategy_lab.risk_guard import (
    RiskGuard,
    RiskGuardConfig,
    RiskGuardVerdict,
)
from trading_system.strategy_lab.scoring import score_metrics

__all__ = [
    "Evaluator",
    "Generator",
    "LabBacktestResult",
    "LabBacktester",
    "LoopController",
    "MCDrawdownFloor",
    "Optimizer",
    "OptimizerConfig",
    "OptimizerDecision",
    "Registry",
    "RegistryEntry",
    "RiskGuard",
    "RiskGuardConfig",
    "RiskGuardVerdict",
    "StaticGenerator",
    "StrategyCandidate",
    "StrategyMetrics",
    "score_metrics",
]
