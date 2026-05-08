"""Tests for ``trading_system.strategy_lab.evaluator``.

REQ refs: REQ_F_MTO_002 (compute metrics), REQ_F_MTO_004
(walk-forward integration), REQ_NF_REP_001 (deterministic).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.backtesting.result import BacktestResult
from trading_system.backtesting.walk_forward import WFResult, WindowResult
from trading_system.capital_flow.flow import CapitalFlow
from trading_system.models.flow import EquityPoint
from trading_system.models.money import Currency, Money
from trading_system.strategy_lab.evaluator import Evaluator

EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(day: int = 1) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def _eq_point(day: int, value: str, dd: str = "0") -> EquityPoint:
    v = _eur(value)
    return EquityPoint(at=_ts(day), equity_gross=v, equity_after_tax=v, drawdown_pct=Decimal(dd))


def _result(curve: tuple[EquityPoint, ...], excl: tuple[Decimal, ...]) -> BacktestResult:
    return BacktestResult(
        trades=(),
        equity_curve=curve,
        equity_excl_injections=excl,
        final_cash=_eur("0"),
        final_equity_after_tax=curve[-1].equity_after_tax if curve else _eur("0"),
        realized_gross=_eur("0"),
        realized_after_tax=_eur("0"),
        dividends_gross=_eur("0"),
        dividends_after_tax=_eur("0"),
        knockouts=0,
        injections_applied=0,
    )


def _empty_result() -> BacktestResult:
    return _result((), ())


def _capital(initial: str = "10000") -> CapitalFlow:
    return CapitalFlow(initial=_eur(initial))


# ---------------------------------------------------------------------------
# Compute on empty result — neutral defaults
# ---------------------------------------------------------------------------


def test_empty_result_yields_zero_return() -> None:
    metrics = Evaluator().compute(_empty_result(), _capital())
    assert metrics.net_after_tax_return == Decimal(0)
    assert metrics.sharpe == Decimal(0)
    assert metrics.max_drawdown == Decimal(0)
    assert metrics.dd_penalty == Decimal(0)


# ---------------------------------------------------------------------------
# Compute on a simple growing curve
# ---------------------------------------------------------------------------


def test_growing_curve_yields_positive_return_and_low_dd() -> None:
    curve = (
        _eq_point(1, "10000", dd="0"),
        _eq_point(2, "10100", dd="0"),
        _eq_point(3, "10250", dd="0"),
        _eq_point(4, "10400", dd="0"),
    )
    excl = (Decimal("10000"), Decimal("10100"), Decimal("10250"), Decimal("10400"))
    metrics = Evaluator().compute(_result(curve, excl), _capital("10000"))
    # Total return = (10400 - 10000) / 10000 = 0.04
    assert metrics.net_after_tax_return == Decimal("0.04")
    assert metrics.return_ == metrics.net_after_tax_return
    assert metrics.max_drawdown == Decimal(0)
    assert metrics.dd_penalty == Decimal(0)
    assert metrics.stability == Decimal(1)
    assert metrics.sharpe > Decimal(0)
    # Vol non-zero on a non-monotone curve.
    assert metrics.risk >= Decimal(0)


# ---------------------------------------------------------------------------
# Drawdown-aware stability + dd_penalty
# ---------------------------------------------------------------------------


def test_drawdown_increases_dd_penalty_decreases_stability() -> None:
    curve = (
        _eq_point(1, "10000", dd="0"),
        _eq_point(2, "11000", dd="0"),
        _eq_point(3, "9000", dd="0.18"),  # ~18% off peak
    )
    excl = (Decimal("10000"), Decimal("11000"), Decimal("9000"))
    metrics = Evaluator().compute(_result(curve, excl), _capital("10000"))
    assert metrics.max_drawdown == Decimal("0.18")
    assert metrics.dd_penalty == Decimal("0.18")
    assert metrics.stability == Decimal("0.82")  # 1 - 0.18


# ---------------------------------------------------------------------------
# Walk-forward integration — regime stability
# ---------------------------------------------------------------------------


def _wf_result(oos_sharpes: list[str]) -> WFResult:
    """Build a synthetic WFResult with given OOS Sharpe values; the
    other fields are placeholders (Evaluator only reads oos_sharpe)."""
    empty_inner = _empty_result()
    windows = tuple(
        WindowResult(
            train_start=_ts(1 + i),
            train_end=_ts(2 + i),
            valid_end=_ts(3 + i),
            oos_end=_ts(4 + i),
            train_sharpe=Decimal("1.0"),
            valid_sharpe=Decimal("1.0"),
            oos_sharpe=Decimal(s),
            train_result=empty_inner,
            valid_result=empty_inner,
            oos_result=empty_inner,
        )
        for i, s in enumerate(oos_sharpes)
    )
    return WFResult(windows=windows, collapsed=False)


def test_uniform_oos_sharpe_yields_perfect_regime_stability() -> None:
    wf = _wf_result(["1.0", "1.0", "1.0"])
    metrics = Evaluator().compute(_empty_result(), _capital(), wf=wf)
    assert metrics.regime_stability == Decimal(1)


def test_volatile_oos_sharpe_lowers_regime_stability() -> None:
    wf = _wf_result(["1.0", "0.0", "2.0"])
    metrics = Evaluator().compute(_empty_result(), _capital(), wf=wf)
    assert metrics.regime_stability < Decimal("0.5")


def test_no_wf_uses_neutral_default() -> None:
    metrics = Evaluator().compute(_empty_result(), _capital())
    # default neutral is 0.5
    assert metrics.regime_stability == Decimal("0.5")


# ---------------------------------------------------------------------------
# Determinism: same inputs -> same metrics
# ---------------------------------------------------------------------------


def test_determinism() -> None:
    curve = (
        _eq_point(1, "10000", dd="0"),
        _eq_point(2, "10500", dd="0"),
    )
    excl = (Decimal("10000"), Decimal("10500"))
    cf = _capital("10000")
    m1 = Evaluator().compute(_result(curve, excl), cf)
    m2 = Evaluator().compute(_result(curve, excl), cf)
    assert m1 == m2
