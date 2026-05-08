"""Integration test: ``Backtest`` runs against ``YFinanceMarketDataProvider``.

The committed fixture under ``tests/data/yfinance/fixtures/ASML.AS/``
provides a tiny synthetic OHLCV slice (4 daily bars in early Jan
2026) plus one dividend event. The integration test:

1. Copies the fixture to a ``tmp_path`` cache root (so writes from
   ``put_bars`` during a separate test path can't pollute the
   committed fixture).
2. Builds a ``YFinanceMarketDataProvider`` with ``allow_network=False``
   over that cache.
3. Runs a ``Backtest`` over the same date range with a no-op
   strategy.
4. Asserts the engine ingests bars via the provider without
   falling back to network and produces a non-empty equity curve.
5. Replays with the same seed against the same fixture and verifies
   bit-identical results (REQ_NF_DET_001 / REQ_F_BCT_001).

The test uses the *real* ``Backtest.assemble`` path and the *real*
``MarketDataProvider`` Protocol — no mocks, no shims — so it
covers REQ_SDS_FLO_003 (live and backtest paths share the same
trade-decision pipeline) for the new adapter.

REQ refs: REQ_F_DAT_001, REQ_NF_DAT_001, REQ_F_BCT_001,
REQ_NF_DET_001, REQ_SDS_FLO_003, REQ_SDS_INT_002.
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trading_system.backtesting import Backtest, BacktestConfig
from trading_system.data.types import Timeframe
from trading_system.data.yfinance import YFinanceCache, YFinanceMarketDataProvider
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
from trading_system.result import Ok
from trading_system.risk.config import RiskConfig
from trading_system.risk.engine import RiskEngine
from trading_system.tax.config import TaxConfig

EUR = Currency.EUR
_FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(year: int = 2026, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _stock() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


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


class _StubSafety:
    def must_halt(self) -> bool:
        return False

    def state(self) -> KillSwitchState:
        return KillSwitchState.ACTIVE

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        pass


class _NoopStrategy:
    """Emits no proposals — keeps the integration test focused on
    the data-feed plumbing."""

    id: StrategyId = StrategyId("noop")

    def evaluate(self, state) -> list[TradeProposal]:
        return []


def _populate_fixture_cache(dest_root: Path) -> YFinanceCache:
    """Copy the committed fixture tree to ``dest_root`` and return a
    cache rooted there."""
    if dest_root.exists():
        shutil.rmtree(dest_root)
    shutil.copytree(_FIXTURE_ROOT, dest_root)
    # The fixture filename was chosen to match the CacheKey.filename()
    # output for these timestamps; we don't need to rename anything.
    return YFinanceCache(root=dest_root)


def _assemble(cache_root: Path, *, seed: int = 1) -> Backtest:
    cache = _populate_fixture_cache(cache_root)
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=EUR,
        allow_network=False,  # REQ_F_DAT_006: hermetic
    )
    cfg = BacktestConfig(
        seed=seed,
        # The fixture covers 2026-01-02..2026-01-07; the cache key in
        # the fixture name is start=2026-01-01 / end=2026-01-08, so
        # we pass exactly those endpoints to the engine.
        start=_ts(2026, 1, 1),
        end=_ts(2026, 1, 8),
        timeframe=Timeframe.D1,
        starting_capital=_eur("10000"),
        tax=TaxConfig.default(),
    )
    s = _stock()
    risk = RiskEngine(cfg=RiskConfig(), safety=_StubSafety())
    res = Backtest.assemble(
        cfg=cfg,
        strategies=(_NoopStrategy(),),
        strategy_buckets={_NoopStrategy.id: AllocationBucket.STOCK},
        instruments=(s,),
        data=provider,
        fee_model=FlatFeeModel(commission=_eur("0"), spread_bps=Decimal(0)),
        slippage_model=ZeroSlippageModel(),
        risk=risk,
        pc=_phase_constraints(),
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok), f"assemble failed: {res}"
    return res.value


# ---------------------------------------------------------------------------
# REQ_SDS_INT_002 — engine consumes YFinanceMarketDataProvider via the Protocol
# ---------------------------------------------------------------------------


def test_engine_runs_against_yfinance_provider_offline(tmp_path: Path) -> None:
    bt = _assemble(tmp_path / "cache")
    result = bt.run()
    # Fixture has 4 bars; the engine records equity once per tick.
    assert len(result.equity_curve) == 4
    # Final equity matches starting capital (no-op strategy).
    assert result.final_equity_after_tax == _eur("10000")


# ---------------------------------------------------------------------------
# REQ_NF_DAT_001 / REQ_F_BCT_001 — fixture replays bit-identical
# ---------------------------------------------------------------------------


def test_replay_against_same_fixture_is_bit_identical(tmp_path: Path) -> None:
    r1 = _assemble(tmp_path / "cache_a", seed=42).run()
    r2 = _assemble(tmp_path / "cache_b", seed=42).run()
    # Same fixture content, same seed -> identical run.
    assert r1.trades == r2.trades
    assert r1.equity_curve == r2.equity_curve
    assert r1.final_cash == r2.final_cash


# ---------------------------------------------------------------------------
# Hermetic — no yfinance / pandas import on this path
# ---------------------------------------------------------------------------


def test_integration_path_remains_hermetic(tmp_path: Path) -> None:
    # Run the engine; nothing in this test should cause yfinance or
    # pandas to be imported. allow_network=False guarantees the
    # network branch never fires; the cache fixture provides every
    # bar the engine reads.
    _assemble(tmp_path / "cache").run()
    assert "yfinance" not in sys.modules
    assert "pandas" not in sys.modules
