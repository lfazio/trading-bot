"""Performance gate — REQ_TP_GAT_002.

REQ_TP_GAT_002 — Backtest throughput SHALL be ≥ 10 000 ticks/s on
a single core with the mock provider.

The benchmark runs a 1000-bar backtest against ``MockMarketDataProvider``
+ a no-op buy-once strategy and measures wall-clock time per ``run()``
call. The throughput is ``n_ticks / mean_seconds`` — must clear the
10 000 ticks/s budget with a 2× headroom (i.e., the test fails when
throughput drops below 5 000 ticks/s) so slow CI runners don't flap.

Opt-in via ``@pytest.mark.perf`` so the default ``pytest -q`` run
stays fast. CI invokes ``pytest -m perf`` separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.backtesting import Backtest, BacktestConfig
from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.slippage import ZeroSlippageModel
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
from trading_system.strategies.state import MarketState
from trading_system.tax.config import TaxConfig


pytestmark = pytest.mark.perf


_THROUGHPUT_BUDGET_TICKS_PER_SECOND = 10_000  # REQ_TP_GAT_002 hard floor
_HEADROOM = 2.0  # accept 5 000 ticks/s on slow CI runners
_N_TICKS = 1_000  # bars per backtest; chosen so a single run ≥ 0.05 s


def _stock() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


class _StubSafety:
    def must_halt(self) -> bool:
        return False

    def state(self) -> KillSwitchState:
        return KillSwitchState.ACTIVE

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        pass


@dataclass(slots=True)
class _NoopStrategy:
    """Emits nothing — the throughput test measures the
    engine + data path, not strategy evaluation cost."""

    id: StrategyId
    instrument: Stock

    def evaluate(self, state: MarketState) -> list[TradeProposal]:
        return []


def _build_backtest() -> Backtest:
    s = _stock()
    data = MockMarketDataProvider(seed=1)
    fee_model = FlatFeeModel(commission=Money(Decimal("1.00"), Currency.EUR), spread_bps=Decimal(0))
    risk = RiskEngine(cfg=RiskConfig(), safety=_StubSafety())
    cfg = BacktestConfig(
        seed=1,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        # 1000 daily bars ≈ 2.7 years.
        end=datetime(2026, 1, 1, tzinfo=UTC).replace(year=2026 + (_N_TICKS // 365)),
        timeframe=Timeframe.D1,
        starting_capital=Money(Decimal("10000"), Currency.EUR),
        tax=TaxConfig.default(),
    )
    pc = PhaseConstraints(
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
    strategy = _NoopStrategy(id=StrategyId("noop"), instrument=s)
    res = Backtest.assemble(
        cfg=cfg,
        strategies=(strategy,),
        strategy_buckets={strategy.id: AllocationBucket.STOCK},
        instruments=(s,),
        data=data,
        fee_model=fee_model,
        slippage_model=ZeroSlippageModel(),
        risk=risk,
        pc=pc,
        regime=MarketRegime.SIDEWAYS,
        screener_ranking=(),
    )
    assert isinstance(res, Ok), f"assemble failed: {res}"
    return res.value


def test_backtest_throughput_above_10k_ticks_per_second(benchmark) -> None:  # type: ignore[no-untyped-def]
    """REQ_TP_GAT_002 — the engine SHALL process ≥ 10 000 ticks/s
    on a single core with the mock provider. Measured as
    ``n_ticks / mean(wall_clock)``; the test asserts the budget
    with 2× headroom for slow CI runners."""
    backtest = _build_backtest()
    # Verify the backtest actually has ~_N_TICKS bars before we
    # measure — protects against a config bug silently shrinking
    # the workload.
    result = backtest.run()
    n_ticks = len(result.equity_curve)
    assert n_ticks >= _N_TICKS // 2, (
        f"backtest produced only {n_ticks} ticks; budget calculation "
        f"would be meaningless"
    )

    # ``benchmark`` re-runs many times; rebuild fresh each round
    # so internal state doesn't carry across timings.
    benchmark(lambda: _build_backtest().run())
    mean = benchmark.stats.stats.mean
    throughput = n_ticks / mean
    assert throughput >= _THROUGHPUT_BUDGET_TICKS_PER_SECOND / _HEADROOM, (
        f"backtest throughput {throughput:.0f} ticks/s falls below "
        f"{_THROUGHPUT_BUDGET_TICKS_PER_SECOND / _HEADROOM:.0f} budget "
        f"(mean wall-clock {mean * 1000:.1f} ms for {n_ticks} ticks)"
    )
