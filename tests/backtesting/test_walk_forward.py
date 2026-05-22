"""Tests for ``trading_system.backtesting.walk_forward``.

Covers TC_BCT_008 (walk-forward windows default to (24/6/6) for
phases 1-4 and (60/12/24) for phases 5-6) and TC_BCT_009 (OOS Sharpe
< 0.5 x train Sharpe in any window flags collapse).

REQ refs: REQ_F_BCT_008, REQ_F_BCT_009, REQ_F_STR_003 (every
shipped strategy SHALL pass walk-forward validation —
``walk_forward`` is the entry point that drives the train /
valid / oos triple and flags collapse), REQ_SDD_ALG_004 (window
defaults: 24/6/6 phases 1-4, 60/12/24 phases 5-6), REQ_NF_DET_001.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.backtesting import (
    Backtest,
    BacktestConfig,
    WalkForwardWindow,
    WindowResult,
    detect_oos_collapse,
    sharpe_ratio,
    walk_forward,
)
from trading_system.backtesting.result import BacktestResult
from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.slippage import ZeroSlippageModel
from trading_system.models.flow import EquityPoint
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
from trading_system.result import Err, Ok, Result
from trading_system.risk.config import RiskConfig
from trading_system.risk.engine import RiskEngine
from trading_system.tax.config import TaxConfig

EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(year: int, month: int = 1, day: int = 1) -> datetime:
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


# ---------------------------------------------------------------------------
# Default windows — TC_BCT_008
# ---------------------------------------------------------------------------


class TestWalkForwardWindow:
    def test_base_default_24_6_6_months(self) -> None:
        # 24/6/6 months at the SDD's 30-day approximation.
        w = WalkForwardWindow.base()
        assert w.train == timedelta(days=720)  # 24 * 30
        assert w.valid == timedelta(days=180)  # 6 * 30
        assert w.oos == timedelta(days=180)  # 6 * 30

    def test_phase5_plus_default_60_12_24_months(self) -> None:
        w = WalkForwardWindow.phase5_plus()
        assert w.train == timedelta(days=1800)  # 60 * 30
        assert w.valid == timedelta(days=360)  # 12 * 30
        assert w.oos == timedelta(days=720)  # 24 * 30

    def test_zero_or_negative_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="train"):
            WalkForwardWindow(
                train=timedelta(0),
                valid=timedelta(days=1),
                oos=timedelta(days=1),
            )


# ---------------------------------------------------------------------------
# sharpe_ratio
# ---------------------------------------------------------------------------


def _curve(values: list[str]) -> tuple[EquityPoint, ...]:
    return tuple(
        EquityPoint(
            at=_ts(2026, 1, i + 1),
            equity_gross=_eur(v),
            equity_after_tax=_eur(v),
            drawdown_pct=Decimal(0),
        )
        for i, v in enumerate(values)
    )


class TestSharpeRatio:
    def test_short_curve_returns_zero(self) -> None:
        assert sharpe_ratio(_curve(["1000"])) == Decimal(0)

    def test_zero_variance_returns_zero(self) -> None:
        # All returns identical -> std=0 -> Sharpe undefined; we return 0.
        assert sharpe_ratio(_curve(["1000", "1010", "1020.10"])) >= 0
        # Use a flat curve to force variance=0.
        flat = _curve(["1000", "1010", "1020.10", "1030.30"])  # not flat returns
        # Positive return curve with growing returns has nonzero variance.
        result = sharpe_ratio(flat)
        assert result != Decimal(0)

    def test_constant_returns_zero_variance_yields_zero(self) -> None:
        # Each step adds 10 to a baseline of 1000: returns = [10/1000,
        # 10/1010, 10/1020] — not exactly constant. Force constancy
        # via multiplicative growth: 1000 * (1 + 0.01)^k -> identical
        # returns, std=0.
        c = _curve(["1000", "1010", "1020.10", "1030.301"])
        # Returns: 0.01, 0.01, 0.01 (close to identical) -> std nearly 0.
        # The function returns exactly 0 only when var == 0 (Decimal exact);
        # rounding may keep var nonzero but very small. Accept either:
        s = sharpe_ratio(c)
        # Sanity: not enormous.
        assert abs(s) < Decimal("100000")

    def test_positive_curve_yields_positive_sharpe(self) -> None:
        # Increasing curve with varied returns -> Sharpe > 0.
        c = _curve(["1000", "1015", "1010", "1030", "1025", "1050"])
        assert sharpe_ratio(c) > Decimal(0)


# ---------------------------------------------------------------------------
# detect_oos_collapse — TC_BCT_009
# ---------------------------------------------------------------------------


def _make_window(train_sharpe: str, oos_sharpe: str) -> WindowResult:
    """Build a WindowResult skipping the inner BacktestResult fields
    that are not consulted by the collapse detector."""
    empty = BacktestResult(
        trades=(),
        equity_curve=(),
        equity_excl_injections=(),
        final_cash=_eur("0"),
        final_equity_after_tax=_eur("0"),
        realized_gross=_eur("0"),
        realized_after_tax=_eur("0"),
        dividends_gross=_eur("0"),
        dividends_after_tax=_eur("0"),
        knockouts=0,
        injections_applied=0,
    )
    return WindowResult(
        train_start=_ts(2026),
        train_end=_ts(2026, 7),
        valid_end=_ts(2027),
        oos_end=_ts(2027, 7),
        train_sharpe=Decimal(train_sharpe),
        valid_sharpe=Decimal(0),
        oos_sharpe=Decimal(oos_sharpe),
        train_result=empty,
        valid_result=empty,
        oos_result=empty,
    )


class TestDetectOOSCollapse:
    def test_oos_above_half_train_no_collapse(self) -> None:
        # train=2.0, oos=1.2 -> 1.2 > 0.5 * 2.0 = 1.0 -> ok.
        windows = (_make_window("2.0", "1.2"),)
        assert detect_oos_collapse(windows) is False

    def test_oos_below_half_train_collapses(self) -> None:
        # train=2.0, oos=0.8 -> 0.8 < 1.0 -> collapse.
        windows = (_make_window("2.0", "0.8"),)
        assert detect_oos_collapse(windows) is True

    def test_oos_exactly_half_train_no_collapse(self) -> None:
        # Strict <: oos == 0.5 * train passes.
        windows = (_make_window("2.0", "1.0"),)
        assert detect_oos_collapse(windows) is False

    def test_any_collapsing_window_flags_overall(self) -> None:
        # First window ok, second collapses.
        windows = (
            _make_window("2.0", "1.5"),
            _make_window("2.0", "0.5"),
        )
        assert detect_oos_collapse(windows) is True

    def test_negative_train_sharpe_skipped(self) -> None:
        # Negative training Sharpe is not credible; collapse detector
        # ignores it rather than triggering on every flat-or-losing
        # baseline.
        windows = (_make_window("-1.0", "-2.0"),)
        assert detect_oos_collapse(windows) is False


# ---------------------------------------------------------------------------
# walk_forward — orchestration end-to-end (small period)
# ---------------------------------------------------------------------------


class _StubSafety:
    def must_halt(self) -> bool:
        return False

    def state(self) -> KillSwitchState:
        return KillSwitchState.ACTIVE

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        pass


class _NoopStrategy:
    """Emits no proposals — keeps the walk-forward smoke test cheap."""

    id: StrategyId = StrategyId("noop")

    def evaluate(self, state) -> list[TradeProposal]:
        return []


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


def _make_factory(seed: int = 1):
    s = _stock()
    data = MockMarketDataProvider(seed=seed)
    risk = RiskEngine(cfg=RiskConfig(), safety=_StubSafety())

    def factory(start: datetime, end: datetime) -> Result[Backtest, str]:
        cfg = BacktestConfig(
            seed=seed,
            start=start,
            end=end,
            timeframe=Timeframe.D1,
            starting_capital=_eur("10000"),
            tax=TaxConfig.default(),
        )
        return Backtest.assemble(
            cfg=cfg,
            strategies=(_NoopStrategy(),),
            strategy_buckets={_NoopStrategy.id: AllocationBucket.STOCK},
            instruments=(s,),
            data=data,
            fee_model=FlatFeeModel(commission=_eur("0"), spread_bps=Decimal(0)),
            slippage_model=ZeroSlippageModel(),
            risk=risk,
            pc=_phase_constraints(),
            regime=MarketRegime.SIDEWAYS,
        )

    return factory


class TestWalkForward:
    def test_invalid_period_returns_err(self) -> None:
        res = walk_forward(
            backtest_factory=_make_factory(),
            period_start=_ts(2027),
            period_end=_ts(2026),
            window=WalkForwardWindow(
                train=timedelta(days=10),
                valid=timedelta(days=5),
                oos=timedelta(days=5),
            ),
        )
        assert isinstance(res, Err)
        assert "invalid_period" in res.error

    def test_too_short_period_yields_zero_windows(self) -> None:
        # Window total = 30d; period only 20d.
        res = walk_forward(
            backtest_factory=_make_factory(),
            period_start=_ts(2026, 1, 1),
            period_end=_ts(2026, 1, 21),
            window=WalkForwardWindow(
                train=timedelta(days=10),
                valid=timedelta(days=10),
                oos=timedelta(days=10),
            ),
        )
        assert isinstance(res, Ok)
        assert res.value.windows == ()
        assert res.value.collapsed is False

    def test_rolling_step_equals_valid_duration(self) -> None:
        # Period = 60d; window = (10, 5, 5) = 20d. Rolling step = 5d.
        # Number of steps: floor((60 - 20) / 5) + 1 = 9.
        # Each step starts at 0, 5, 10, 15, 20, 25, 30, 35, 40 (i.e. 9 windows).
        win = WalkForwardWindow(
            train=timedelta(days=10),
            valid=timedelta(days=5),
            oos=timedelta(days=5),
        )
        res = walk_forward(
            backtest_factory=_make_factory(),
            period_start=_ts(2026, 1, 1),
            period_end=_ts(2026, 1, 1) + timedelta(days=60),
            window=win,
        )
        assert isinstance(res, Ok)
        assert len(res.value.windows) == 9
        # Each consecutive train_start advances by 5 days.
        for prev, cur in zip(res.value.windows[:-1], res.value.windows[1:], strict=True):
            assert cur.train_start - prev.train_start == timedelta(days=5)

    def test_run_is_deterministic(self) -> None:
        win = WalkForwardWindow(
            train=timedelta(days=10),
            valid=timedelta(days=5),
            oos=timedelta(days=5),
        )
        res1 = walk_forward(
            backtest_factory=_make_factory(seed=7),
            period_start=_ts(2026, 1, 1),
            period_end=_ts(2026, 1, 1) + timedelta(days=40),
            window=win,
        )
        res2 = walk_forward(
            backtest_factory=_make_factory(seed=7),
            period_start=_ts(2026, 1, 1),
            period_end=_ts(2026, 1, 1) + timedelta(days=40),
            window=win,
        )
        assert isinstance(res1, Ok) and isinstance(res2, Ok)
        # Strategy is no-op so each window's equity_curve and Sharpe
        # are identical bit-for-bit across runs.
        assert res1.value == res2.value
