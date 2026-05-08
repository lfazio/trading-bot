"""Deterministic backtesting engine + sub-simulators.

Phase 5 step 10. Implements the SDD §6 design: an event-driven engine
that replays a tick stream through the same trade-decision pipeline
the live runtime uses, applying fees, slippage, dividends, knockouts,
the 30 % CTO tax, and an explicit external-capital-injection timeline.

REQ refs: REQ_F_BCT_001..009, REQ_SDS_MOD_013, REQ_SDD_ALG_019,
REQ_SDD_PER_004, REQ_NF_DET_001.
"""

from trading_system.backtesting.broker import BacktestBroker
from trading_system.backtesting.clock import EventClock
from trading_system.backtesting.config import BacktestConfig
from trading_system.backtesting.dividends import DividendSimulator
from trading_system.backtesting.engine import Backtest
from trading_system.backtesting.injection_scheduler import InjectionScheduler
from trading_system.backtesting.knockout import KnockoutSimulator
from trading_system.backtesting.market_replay import MarketReplay
from trading_system.backtesting.result import BacktestResult

__all__ = [
    "Backtest",
    "BacktestBroker",
    "BacktestConfig",
    "BacktestResult",
    "DividendSimulator",
    "EventClock",
    "InjectionScheduler",
    "KnockoutSimulator",
    "MarketReplay",
]
