"""``Backtest`` — the deterministic backtest engine.

Orchestrates the per-tick pipeline:

  inject -> dividends -> tick -> mark -> evaluate strategies ->
  tax-aware gate -> risk gate -> submit -> apply to Portfolio ->
  knockout check -> record equity

Per REQ_SDS_FLO_003 the same trade-decision pipeline runs in live
mode; only the adapters differ (``BacktestBroker`` here vs. a live
``BrokerAdapter`` implementation in production).

REQ refs:
- REQ_F_BCT_001 / REQ_NF_DET_001 — deterministic given (seed, config,
  data); ``random.seed(cfg.seed)`` at the top of ``run()``.
- REQ_F_BCT_002..006 — fees, slippage, knockout, dividends, tax all
  driven by their dedicated simulators / models.
- REQ_F_BCT_007 — explicit injection schedule replay.
- REQ_SDS_ARC_002 — pure-engine principle: the engine itself is a
  pure orchestration of inputs; all I/O is pushed to adapters.
- REQ_SDS_ARC_005 — seed lives in BacktestConfig.
- REQ_SDS_ARC_006 — time via EventClock; no wall-clock calls.
- REQ_SDD_ALG_019 — tick ordering deterministic (delegated to
  MarketReplay).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from trading_system.backtesting.broker import BacktestBroker
from trading_system.backtesting.clock import EventClock
from trading_system.backtesting.config import BacktestConfig
from trading_system.backtesting.dividends import DividendSimulator
from trading_system.backtesting.injection_scheduler import InjectionScheduler
from trading_system.backtesting.knockout import KnockoutSimulator
from trading_system.backtesting.market_replay import MarketReplay
from trading_system.backtesting.result import BacktestResult
from trading_system.capital_flow.flow import CapitalFlow
from trading_system.data.provider import MarketDataProvider
from trading_system.execution.fees import FeeModel
from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.slippage import SlippageModel
from trading_system.models.identifiers import OrderId, StrategyId
from trading_system.models.instrument import Instrument
from trading_system.models.meta import TradeProposal
from trading_system.models.phase import AllocationBucket, MarketRegime, PhaseConstraints
from trading_system.models.trading import Order, OrderType, Trade
from trading_system.portfolio.portfolio import Portfolio
from trading_system.result import Err, Ok, Result
from trading_system.risk.engine import RiskEngine
from trading_system.screener.engine import ScoredStock
from trading_system.strategies.protocol import Strategy
from trading_system.strategies.state import MarketState
from trading_system.tax.engine import trade_passes_gate


@dataclass(slots=True)
class Backtest:
    """Single-run backtest engine. Construct via ``assemble`` and then
    call ``run()``."""

    cfg: BacktestConfig
    strategies: tuple[Strategy, ...]
    strategy_buckets: dict[StrategyId, AllocationBucket]
    instruments: tuple[Instrument, ...]
    data: MarketDataProvider
    fee_model: FeeModel
    slippage_model: SlippageModel
    risk: RiskEngine
    pc: PhaseConstraints
    regime: MarketRegime
    screener_ranking: tuple[ScoredStock, ...]
    # Wired-up state (populated by ``assemble``):
    market_replay: MarketReplay
    broker: BacktestBroker
    portfolio: Portfolio
    capflow: CapitalFlow
    clock: EventClock
    injsched: InjectionScheduler
    divs: DividendSimulator
    knockout: KnockoutSimulator
    _trades: list[Trade] = field(default_factory=list)
    _orders: dict[OrderId, Order] = field(default_factory=dict)
    _injections_applied: int = 0
    _knockouts: int = 0
    _next_order_seq: int = 0

    @classmethod
    def assemble(  # noqa: PLR0913 — orchestration entry point
        cls,
        *,
        cfg: BacktestConfig,
        strategies: tuple[Strategy, ...],
        strategy_buckets: dict[StrategyId, AllocationBucket],
        instruments: tuple[Instrument, ...],
        data: MarketDataProvider,
        fee_model: FeeModel,
        slippage_model: SlippageModel,
        risk: RiskEngine,
        pc: PhaseConstraints,
        regime: MarketRegime,
        screener_ranking: tuple[ScoredStock, ...] = (),
    ) -> Result[Backtest, str]:
        # Pre-load tick stream.
        replay_res = MarketReplay.try_new(
            data,
            instruments,
            cfg.timeframe,
            cfg.start,
            cfg.end,
            cfg.spread_pct,
        )
        if isinstance(replay_res, Err):
            return Err(replay_res.error)
        replay = replay_res.value

        # Build the broker around a freshly-seeded LocalBrokerAdapter.
        adapter = LocalBrokerAdapter(
            starting_cash=cfg.starting_capital,
            fee_model=fee_model,
            slippage_model=slippage_model,
            seed=cfg.seed,
        )
        for instr in instruments:
            adapter.register_instrument(instr)

        return Ok(
            cls(
                cfg=cfg,
                strategies=strategies,
                strategy_buckets=strategy_buckets,
                instruments=instruments,
                data=data,
                fee_model=fee_model,
                slippage_model=slippage_model,
                risk=risk,
                pc=pc,
                regime=regime,
                screener_ranking=screener_ranking,
                market_replay=replay,
                broker=BacktestBroker(adapter=adapter),
                portfolio=Portfolio.empty(cfg.starting_capital),
                capflow=CapitalFlow(initial=cfg.starting_capital, injections=[]),
                clock=EventClock(),
                injsched=InjectionScheduler.from_schedule(cfg.injection_schedule),
                divs=DividendSimulator(data=data),
                knockout=KnockoutSimulator(),
            )
        )

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    def run(self) -> BacktestResult:
        """Drive the engine to completion and return a BacktestResult."""
        portfolio = self.portfolio
        random.seed(self.cfg.seed)  # REQ_SDS_ARC_005

        for tick in self.market_replay.stream(self.clock):
            # 1. Apply due injections (REQ_F_BCT_007).
            applied = self.injsched.maybe_apply(tick.at, self.capflow, portfolio)
            self._injections_applied += len(applied)

            # 2. Forward the tick to the broker so latest-tick is fresh
            #    *before* any submit attempts.
            self.broker.process_tick(tick)

            # 3. Mark the portfolio at the tick's last price so
            #    equity reads (gates, dashboards) are current.
            portfolio.mark({tick.instrument_id: tick.last})

            # 4. Apply due dividends (REQ_F_BCT_005).
            self.divs.maybe_apply(tick.at, portfolio, self.cfg.tax)

            # 5. Knockout sweep (REQ_F_BCT_004).
            knocked = self.knockout.maybe_trigger(tick, portfolio, self.cfg.tax)
            self._knockouts += len(knocked)

            # 6. Strategy evaluation -> proposals -> gates -> submit.
            state = MarketState(
                at=self.clock.now(),
                portfolio=portfolio,
                constraints=self.pc,
                regime=self.regime,
                screener_ranking=self.screener_ranking,
                market=self.data,
            )
            for strategy in self.strategies:
                proposals = strategy.evaluate(state)
                for proposal in proposals:
                    self._maybe_execute(proposal, strategy, tick)

            # 7. Record equity. We snapshot every tick for the highest
            #    fidelity equity curve; analytics can downsample if a
            #    coarser grain is wanted.
            portfolio.record_equity(tick.at)

        return self._build_result(portfolio)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _maybe_execute(
        self,
        proposal: TradeProposal,
        strategy: Strategy,
        tick,
    ) -> None:
        # Tax-aware gate (REQ_F_TAX_003).
        if not trade_passes_gate(
            self.cfg.tax, proposal.expected_net_profit, proposal.expected_fees
        ):
            return

        # Risk gate (REQ_SDD_ALG_016 ordering).
        portfolio = self.portfolio
        verdict = self.risk.pre_trade(proposal, portfolio, self.pc, self.regime)
        if not verdict.passed:
            return

        # Convert the proposal to a concrete Order. Quantity is a
        # function of equity, the proposal's allocation %, and the
        # current tick price.
        equity = portfolio.equity_after_tax().amount
        if equity <= 0 or tick.last <= 0:
            return
        raw_qty = (equity * proposal.size_pct_of_capital) / tick.last
        if raw_qty <= 0:
            return

        self._next_order_seq += 1
        order = Order(
            id=OrderId(f"bt-{self._next_order_seq:08d}"),
            instrument=proposal.instrument,
            side=proposal.side,
            quantity=raw_qty,
            type=OrderType.MARKET,
            stop_loss=proposal.stop_loss,
            created_at=self.clock.now(),
            source_strategy=strategy.id,
        )

        match self.broker.submit(order):
            case Ok(trade):
                bucket = self.strategy_buckets.get(strategy.id)
                assert bucket is not None, (
                    f"Backtest._maybe_execute: no bucket for strategy {strategy.id}"
                )
                self._orders[order.id] = order
                self._trades.append(trade)
                portfolio.apply(trade, order, bucket, self.cfg.tax)
            case Err(_):
                # Broker rejected (e.g., insufficient cash, currency
                # mismatch). Backtest discards silently — the live
                # path would log + escalate via the safety layer; the
                # backtest result already excludes the trade because
                # nothing was applied to the portfolio.
                return

    def _build_result(self, portfolio: Portfolio) -> BacktestResult:
        equity_excl = self.capflow.equity_excl_injections(portfolio.equity_curve)
        return BacktestResult(
            trades=tuple(self._trades),
            equity_curve=tuple(portfolio.equity_curve),
            equity_excl_injections=tuple(equity_excl),
            final_cash=portfolio.cash(),
            final_equity_after_tax=portfolio.equity_after_tax()
            if portfolio.equity_curve
            else portfolio.cash(),
            realized_gross=portfolio.realized_gross(),
            realized_after_tax=portfolio.realized_after_tax(),
            dividends_gross=portfolio.dividends_gross(),
            dividends_after_tax=portfolio.dividends_after_tax(),
            knockouts=self._knockouts,
            injections_applied=self._injections_applied,
        )
