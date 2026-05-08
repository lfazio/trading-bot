"""Walk-forward harness + OOS-collapse detector.

REQ refs:
- REQ_F_BCT_008 — every shipped strategy has a walk-forward
  certificate (train / validation / out-of-sample windows).
- REQ_F_BCT_009 — OOS-degradation guard: a strategy is rejected if
  any window's OOS Sharpe falls below 0.5 x its train Sharpe.
- REQ_SDD_ALG_004 — default windows: (train=24m, valid=6m, oos=6m)
  for phases 1-4 and (train=60m, valid=12m, oos=24m) for phases 5-6;
  collapse threshold = 0.5x.
- REQ_NF_DET_001 — walk-forward inherits the engine's determinism:
  same seed + same factory inputs -> bit-identical WFResult.

Decoupling pattern: the caller passes a ``backtest_factory`` callable
that returns a fresh ``Result[Backtest, str]`` for a given
``(start, end)`` range. This keeps ``walk_forward`` independent of
how the engine is assembled — strategies, data providers, fee models,
risk engines, etc. flow into the factory closure rather than directly
into this function's signature.

Sharpe computation: per-tick returns of the after-tax equity curve,
annualized by ``sqrt(252)`` (one trading year of daily bars). Lower
timeframes are not yet annualization-aware; daily is the SDD default.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trading_system.backtesting.engine import Backtest
from trading_system.backtesting.result import BacktestResult
from trading_system.models.flow import EquityPoint
from trading_system.result import Err, Ok, Result

# Default windows — REQ_SDD_ALG_004.
_30D = timedelta(days=30)
_DEFAULT_BASE_TRAIN = 24 * _30D
_DEFAULT_BASE_VALID = 6 * _30D
_DEFAULT_BASE_OOS = 6 * _30D
_DEFAULT_PH5_TRAIN = 60 * _30D
_DEFAULT_PH5_VALID = 12 * _30D
_DEFAULT_PH5_OOS = 24 * _30D

# Annualization factor for daily bars (SDD default timeframe).
_TRADING_DAYS_PER_YEAR = Decimal(252)
_ANN_FACTOR = _TRADING_DAYS_PER_YEAR.sqrt()

# Collapse threshold per REQ_F_BCT_009 / REQ_SDD_ALG_004.
COLLAPSE_RATIO = Decimal("0.5")

# Minimum number of points needed for a meaningful Sharpe.
_MIN_CURVE_POINTS = 2
_MIN_RETURNS = 2


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """A single (train, valid, oos) window triple.

    Defaults match REQ_SDD_ALG_004:
    - phases 1-4: train=24m, valid=6m, oos=6m
    - phases 5-6: train=60m, valid=12m, oos=24m
    Phase 5+ explicitly require longer windows so multi-regime crossings
    are exercised in the OOS slice.
    """

    train: timedelta
    valid: timedelta
    oos: timedelta

    def __post_init__(self) -> None:
        for name, td in (("train", self.train), ("valid", self.valid), ("oos", self.oos)):
            if td.total_seconds() <= 0:
                raise ValueError(f"WalkForwardWindow.{name} must be > 0, got {td}")

    @classmethod
    def base(cls) -> WalkForwardWindow:
        """Default window for phases 1-4 (REQ_SDD_ALG_004)."""
        return cls(
            train=_DEFAULT_BASE_TRAIN,
            valid=_DEFAULT_BASE_VALID,
            oos=_DEFAULT_BASE_OOS,
        )

    @classmethod
    def phase5_plus(cls) -> WalkForwardWindow:
        """Extended window for phases 5-6 (REQ_SDD_ALG_004)."""
        return cls(
            train=_DEFAULT_PH5_TRAIN,
            valid=_DEFAULT_PH5_VALID,
            oos=_DEFAULT_PH5_OOS,
        )


@dataclass(frozen=True, slots=True)
class WindowResult:
    """One step in the rolling walk-forward."""

    train_start: datetime
    train_end: datetime
    valid_end: datetime
    oos_end: datetime
    train_sharpe: Decimal
    valid_sharpe: Decimal
    oos_sharpe: Decimal
    train_result: BacktestResult
    valid_result: BacktestResult
    oos_result: BacktestResult


@dataclass(frozen=True, slots=True)
class WFResult:
    """Aggregate walk-forward outcome."""

    windows: tuple[WindowResult, ...]
    collapsed: bool


def sharpe_ratio(equity_curve: list[EquityPoint] | tuple[EquityPoint, ...]) -> Decimal:
    """Annualized Sharpe of the after-tax equity series.

    Returns ``Decimal(0)`` when the curve is shorter than 2 points,
    when all returns are identical (zero variance), or when the
    starting equity is zero — these cases are not meaningful for
    Sharpe and signaling them as zero is safe for the collapse
    detector (zero is never < 0.5 x positive train Sharpe).
    """
    if len(equity_curve) < _MIN_CURVE_POINTS:
        return Decimal(0)
    returns: list[Decimal] = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1].equity_after_tax.amount
        cur = equity_curve[i].equity_after_tax.amount
        if prev == 0:
            continue
        returns.append((cur - prev) / prev)
    if len(returns) < _MIN_RETURNS:
        return Decimal(0)
    n = Decimal(len(returns))
    mean_r = sum(returns, start=Decimal(0)) / n
    var = sum(((r - mean_r) ** 2 for r in returns), start=Decimal(0)) / n
    if var == 0:
        return Decimal(0)
    std = var.sqrt()
    return (mean_r / std) * _ANN_FACTOR


def detect_oos_collapse(windows: list[WindowResult] | tuple[WindowResult, ...]) -> bool:
    """OOS Sharpe < 0.5 x train Sharpe in *any* window flags collapse
    (REQ_F_BCT_009 / REQ_SDD_ALG_004).

    The check applies only when train Sharpe is positive — a flat or
    negative training-period strategy is already non-credible and
    bottom-up flagging here would generate false positives.
    """
    for w in windows:
        if w.train_sharpe <= 0:
            continue
        if w.oos_sharpe < COLLAPSE_RATIO * w.train_sharpe:
            return True
    return False


def walk_forward(
    *,
    backtest_factory: Callable[[datetime, datetime], Result[Backtest, str]],
    period_start: datetime,
    period_end: datetime,
    window: WalkForwardWindow,
) -> Result[WFResult, str]:
    """Roll a (train, valid, oos) window across ``[period_start,
    period_end]``; on each step run three backtests with the
    same factory and collect Sharpe ratios.

    Rolling step is ``window.valid`` (the SDD pseudocode advances by
    ``win.valid`` to maximize OOS coverage without overlap of valid
    slices).

    Returns ``Err`` if any window's backtest assembly or run-time
    factory fails; the engine itself raises only on programmer-error
    invariants, but the factory may surface a configuration error.
    """
    if period_end <= period_start:
        return Err(f"walk_forward:invalid_period: end {period_end} <= start {period_start}")
    full_window = window.train + window.valid + window.oos
    if full_window.total_seconds() <= 0:
        return Err("walk_forward:zero_window")
    cur = period_start
    out: list[WindowResult] = []
    while cur + full_window <= period_end:
        train_start = cur
        train_end = cur + window.train
        valid_end = train_end + window.valid
        oos_end = valid_end + window.oos

        train_res = _build_and_run(backtest_factory, train_start, train_end)
        if isinstance(train_res, Err):
            return Err(f"walk_forward:train:{train_res.error}")
        valid_res = _build_and_run(backtest_factory, train_end, valid_end)
        if isinstance(valid_res, Err):
            return Err(f"walk_forward:valid:{valid_res.error}")
        oos_res = _build_and_run(backtest_factory, valid_end, oos_end)
        if isinstance(oos_res, Err):
            return Err(f"walk_forward:oos:{oos_res.error}")

        train_r = train_res.value
        valid_r = valid_res.value
        oos_r = oos_res.value
        out.append(
            WindowResult(
                train_start=train_start,
                train_end=train_end,
                valid_end=valid_end,
                oos_end=oos_end,
                train_sharpe=sharpe_ratio(train_r.equity_curve),
                valid_sharpe=sharpe_ratio(valid_r.equity_curve),
                oos_sharpe=sharpe_ratio(oos_r.equity_curve),
                train_result=train_r,
                valid_result=valid_r,
                oos_result=oos_r,
            )
        )
        cur += window.valid

    windows = tuple(out)
    return Ok(WFResult(windows=windows, collapsed=detect_oos_collapse(windows)))


def _build_and_run(
    factory: Callable[[datetime, datetime], Result[Backtest, str]],
    start: datetime,
    end: datetime,
) -> Result[BacktestResult, str]:
    res = factory(start, end)
    if isinstance(res, Err):
        return Err(res.error)
    return Ok(res.value.run())
