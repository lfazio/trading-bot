"""``LabBacktester`` — batch-mode wrapper around ``Backtest.assemble``.

Given a ``StrategyCandidate`` and the engine's other ingredients
(data, fees, slippage, risk, phase, regime), the lab backtester
runs a single backtest and returns a ``(BacktestResult, CapitalFlow)``
pair. The ``CapitalFlow`` is needed downstream by ``Evaluator`` so
the canonical equity-excl-injections series is the source of truth
for return.

This wrapper is the ONLY place the strategy_lab pipeline calls into
``backtesting/``. Per REQ_SDS_MOD_014 the runtime SHALL NOT import
this module — meta-optimization runs are operator-triggered, not
runtime-triggered.

REQ refs: REQ_F_MTO_002 (step 2: backtest each candidate),
REQ_F_BCT_001 / REQ_NF_DET_001 (deterministic with seed),
REQ_SDS_FLO_003 (same engine pipeline as live).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.backtesting.config import BacktestConfig
from trading_system.backtesting.engine import Backtest
from trading_system.backtesting.result import BacktestResult
from trading_system.capital_flow.flow import CapitalFlow
from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.execution.fees import FeeModel
from trading_system.execution.slippage import SlippageModel
from trading_system.models.flow import Injection
from trading_system.models.instrument import Instrument
from trading_system.models.money import Money
from trading_system.models.phase import MarketRegime, PhaseConstraints
from trading_system.result import Err, Ok, Result
from trading_system.risk.engine import RiskEngine
from trading_system.screener.engine import ScoredStock
from trading_system.strategy_lab.candidate import StrategyCandidate
from trading_system.tax.config import TaxConfig


@dataclass(frozen=True, slots=True)
class LabBacktestResult:
    """Lab-backtester output bundling everything the evaluator needs."""

    result: BacktestResult
    capital_flow: CapitalFlow


@dataclass(slots=True)
class LabBacktester:
    """Holds the per-cycle invariants; ``run(candidate)`` does the work.

    Construction parameters mirror ``Backtest.assemble``'s engine
    ingredients minus the strategy (which comes from the candidate)
    and minus the seed (which the candidate carries).
    """

    instruments: tuple[Instrument, ...]
    data: MarketDataProvider
    fee_model: FeeModel
    slippage_model: SlippageModel
    risk: RiskEngine
    pc: PhaseConstraints
    regime: MarketRegime
    tax: TaxConfig
    starting_capital: Money
    start: datetime
    end: datetime
    timeframe: Timeframe = Timeframe.D1
    spread_pct: Decimal = Decimal(0)
    injection_schedule: tuple[Injection, ...] = ()
    screener_ranking: tuple[ScoredStock, ...] = ()

    def run(self, candidate: StrategyCandidate) -> Result[LabBacktestResult, str]:
        """Build a ``Backtest`` for ``candidate`` and run it."""
        cfg = BacktestConfig(
            seed=candidate.seed,
            start=self.start,
            end=self.end,
            timeframe=self.timeframe,
            starting_capital=self.starting_capital,
            tax=self.tax,
            injection_schedule=self.injection_schedule,
            spread_pct=self.spread_pct,
        )
        strategy = candidate.strategy_factory()
        assemble_res = Backtest.assemble(
            cfg=cfg,
            strategies=(strategy,),
            strategy_buckets={candidate.id: candidate.bucket},
            instruments=self.instruments,
            data=self.data,
            fee_model=self.fee_model,
            slippage_model=self.slippage_model,
            risk=self.risk,
            pc=self.pc,
            regime=self.regime,
            screener_ranking=self.screener_ranking,
        )
        if isinstance(assemble_res, Err):
            return Err(f"lab_backtester:assemble:{candidate.id}:{assemble_res.error}")
        backtest = assemble_res.value
        result = backtest.run()
        return Ok(
            LabBacktestResult(
                result=result,
                capital_flow=backtest.capflow,
            )
        )
