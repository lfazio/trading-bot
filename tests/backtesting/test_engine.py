"""End-to-end tests for ``trading_system.backtesting.engine``.

Covers TC_BCT_001 (determinism), TC_BCT_002 (fee model), TC_BCT_003
(slippage seeded), TC_BCT_006 (tax at every realization), TC_BCT_007
(backtest reuses the live decision pipeline — same Strategy /
RiskEngine / TaxConfig types).

REQ refs:
- REQ_F_BCT_001 — deterministic given seed + inputs.
- REQ_F_BCT_002 — fees come from FeeModel; Trade.fees carries them.
- REQ_F_BCT_003 — slippage is deterministic for a given seed.
- REQ_F_BCT_006 — tax applied at every realization.
- REQ_F_BCT_007 — explicit injection schedule replay.
- REQ_NF_DET_001 — full determinism end-to-end.
- REQ_SDS_FLO_003 — backtest reuses the live decision pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_system.backtesting import Backtest, BacktestConfig
from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.slippage import GaussianSlippageModel, ZeroSlippageModel
from trading_system.models.identifiers import InstrumentId, StrategyId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.phase import (
    AllocationBucket,
    MarketRegime,
    PhaseConstraints,
)
from trading_system.models.safety import KillSwitchState, KillSwitchTrigger
from trading_system.models.trading import Side, StopLoss
from trading_system.result import Ok
from trading_system.risk.config import RiskConfig
from trading_system.risk.engine import RiskEngine
from trading_system.tax.config import TaxConfig
from trading_system.tax.engine import net_gain

if TYPE_CHECKING:
    from trading_system.strategies.state import MarketState

EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def _stock(symbol: str = "ASML", iid: str = "ASML.AS") -> Stock:
    return Stock(
        id=InstrumentId(iid),
        symbol=symbol,
        exchange="AS",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


# ---------------------------------------------------------------------------
# Test doubles — same shape as tests/risk/test_engine.py
# ---------------------------------------------------------------------------


class _StubSafety:
    def must_halt(self) -> bool:
        return False

    def state(self) -> KillSwitchState:
        return KillSwitchState.ACTIVE

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        pass


@dataclass(slots=True)
class _BuyOnceStrategy:
    """Emits a single BUY proposal on its first ``evaluate`` and
    nothing thereafter."""

    id: StrategyId
    instrument: Stock
    size_pct: Decimal
    expected_fees: Money
    expected_net_profit: Money
    stop_loss_price: Decimal = field(default_factory=lambda: Decimal("40"))
    _emitted: bool = field(default=False)

    def evaluate(self, state: MarketState) -> list[TradeProposal]:
        if self._emitted:
            return []
        self._emitted = True
        return [
            TradeProposal(
                instrument=self.instrument,
                side=Side.BUY,
                size_pct_of_capital=self.size_pct,
                expected_net_profit=self.expected_net_profit,
                expected_fees=self.expected_fees,
                stop_loss=StopLoss(price=self.stop_loss_price),
                source_strategy=self.id,
            )
        ]


def _phase_constraints() -> PhaseConstraints:
    return PhaseConstraints(
        max_positions=6,
        max_trades_per_month=8,
        allocation_targets={
            AllocationBucket.STOCK: Decimal("0.50"),
            AllocationBucket.TACTICAL: Decimal("0.20"),
            AllocationBucket.CASH: Decimal("0.30"),
        },
        turbo_exposure_max=Decimal(0),
        risk_per_trade_band=(Decimal("0.005"), Decimal("0.05")),
        max_drawdown=Decimal("0.15"),
    )


def _build(
    seed: int = 1,
    *,
    use_slippage: bool = False,
    fee_commission: str = "1.00",
    fee_spread_bps: str = "0",
) -> Backtest:
    s = _stock()
    data = MockMarketDataProvider(seed=seed)
    fee_model = FlatFeeModel(commission=_eur(fee_commission), spread_bps=Decimal(fee_spread_bps))
    slip_model = (
        GaussianSlippageModel(stdev_pct=Decimal("0.001")) if use_slippage else ZeroSlippageModel()
    )
    risk = RiskEngine(cfg=RiskConfig(), safety=_StubSafety())
    cfg = BacktestConfig(
        seed=seed,
        start=_ts(1),
        end=_ts(10),
        timeframe=Timeframe.D1,
        starting_capital=_eur("10000"),
        tax=TaxConfig.default(),
    )
    strategy = _BuyOnceStrategy(
        id=StrategyId("buyonce"),
        instrument=s,
        size_pct=Decimal("0.01"),
        expected_fees=_eur("1.00"),
        expected_net_profit=_eur("10.00"),
    )
    res = Backtest.assemble(
        cfg=cfg,
        strategies=(strategy,),
        strategy_buckets={strategy.id: AllocationBucket.STOCK},
        instruments=(s,),
        data=data,
        fee_model=fee_model,
        slippage_model=slip_model,
        risk=risk,
        pc=_phase_constraints(),
        regime=MarketRegime.SIDEWAYS,
        screener_ranking=(),
    )
    assert isinstance(res, Ok), f"assemble failed: {res}"
    return res.value


# ---------------------------------------------------------------------------
# TC_BCT_001 — Determinism
#
# REQ refs verified by ``test_same_seed_yields_identical_trades``:
# - REQ_NF_REP_001 — replay determinism.
# - REQ_SDD_TST_006 — backtest reproducibility SHALL be asserted
#   by running each shipped strategy twice with the same
#   (seed, config_hash, data) and diffing trade logs + equity
#   curves; any difference SHALL fail the build.
# - REQ_TP_GAT_003 — CI SHALL diff and fail on any difference for
#   two runs with identical inputs.
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_yields_identical_trades(self) -> None:
        r1 = _build(seed=42).run()
        r2 = _build(seed=42).run()
        assert r1.trades == r2.trades
        assert r1.equity_curve == r2.equity_curve
        assert r1.equity_excl_injections == r2.equity_excl_injections
        assert r1.realized_gross == r2.realized_gross
        assert r1.realized_after_tax == r2.realized_after_tax

    def test_different_seeds_can_differ_with_slippage(self) -> None:
        r1 = _build(seed=1, use_slippage=True).run()
        r2 = _build(seed=2, use_slippage=True).run()
        # At least one fill price should differ when slippage is active.
        assert r1.trades and r2.trades
        # In rare seeds Gaussian draws happen to overlap; assert that
        # *something* differs in the result tuple.
        assert (r1.trades, r1.equity_curve) != (r2.trades, r2.equity_curve)


# ---------------------------------------------------------------------------
# TC_BCT_002 — Fee model is consulted; fees end up on Trade.fees
# ---------------------------------------------------------------------------


class TestFeeModel:
    def test_trade_fees_match_flat_commission_when_zero_spread(self) -> None:
        result = _build(fee_commission="2.50", fee_spread_bps="0").run()
        assert result.trades, "expected at least one trade"
        for trade in result.trades:
            assert trade.fees == _eur("2.50"), (
                f"Trade.fees {trade.fees} should match FlatFeeModel commission"
            )


# ---------------------------------------------------------------------------
# TC_BCT_006 — Tax applied at every realization
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _BuyThenSellStrategy:
    """First evaluate returns a BUY; subsequent ones a single SELL on
    a held long position."""

    id: StrategyId
    instrument: Stock
    size_pct: Decimal
    expected_fees: Money
    expected_net_profit: Money
    _state: str = "buy"

    def evaluate(self, state: MarketState) -> list[TradeProposal]:
        if self._state == "buy":
            self._state = "sell-pending"
            return [
                TradeProposal(
                    instrument=self.instrument,
                    side=Side.BUY,
                    size_pct_of_capital=self.size_pct,
                    expected_net_profit=self.expected_net_profit,
                    expected_fees=self.expected_fees,
                    stop_loss=StopLoss(price=Decimal("40")),
                    source_strategy=self.id,
                )
            ]
        if self._state == "sell-pending" and state.portfolio.holds(self.instrument.id):
            self._state = "done"
            return [
                TradeProposal(
                    instrument=self.instrument,
                    side=Side.SELL,
                    size_pct_of_capital=self.size_pct,
                    expected_net_profit=self.expected_net_profit,
                    expected_fees=self.expected_fees,
                    stop_loss=StopLoss(price=Decimal("40")),
                    source_strategy=self.id,
                )
            ]
        return []


def test_tax_applied_at_realization() -> None:
    s = _stock()
    data = MockMarketDataProvider(seed=42)
    risk = RiskEngine(cfg=RiskConfig(), safety=_StubSafety())
    strategy = _BuyThenSellStrategy(
        id=StrategyId("buy-then-sell"),
        instrument=s,
        size_pct=Decimal("0.05"),
        expected_fees=_eur("1.00"),
        expected_net_profit=_eur("10.00"),
    )
    cfg = BacktestConfig(
        seed=42,
        start=_ts(1),
        end=_ts(20),
        timeframe=Timeframe.D1,
        starting_capital=_eur("10000"),
        tax=TaxConfig.default(),
    )
    res = Backtest.assemble(
        cfg=cfg,
        strategies=(strategy,),
        strategy_buckets={strategy.id: AllocationBucket.STOCK},
        instruments=(s,),
        data=data,
        fee_model=FlatFeeModel(commission=_eur("0"), spread_bps=Decimal(0)),
        slippage_model=ZeroSlippageModel(),
        risk=risk,
        pc=_phase_constraints(),
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok)
    result = res.value.run()
    # At least one buy+sell pair fired.
    assert len(result.trades) >= 2
    # Realized gross may be positive or negative depending on the
    # mock RNG. The invariant: realized_after_tax == net_gain(gross).
    # net_gain rounds HALF-UP to cents and treats losses as passthrough.
    expected_net = net_gain(cfg.tax, result.realized_gross)
    assert result.realized_after_tax == expected_net, (
        f"realized_after_tax {result.realized_after_tax} != "
        f"net_gain({result.realized_gross}) = {expected_net}"
    )


# ---------------------------------------------------------------------------
# Sanity: equity curve recorded; injection counter zero by default
# ---------------------------------------------------------------------------


def test_equity_curve_populated_per_tick() -> None:
    bt = _build(seed=7)
    result = bt.run()
    # 10 days x 1 instrument = 10 ticks => 10 equity points.
    assert len(result.equity_curve) == 10
    assert result.injections_applied == 0
    assert result.knockouts == 0
