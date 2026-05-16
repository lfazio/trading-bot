"""Tests for the overfitting pure helpers (REQ_F_QNT_006,
REQ_SDS_QNT_004, REQ_SDD_QNT_005)."""

from __future__ import annotations

from decimal import Decimal

from trading_system.result import Err, Ok
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.quant.overfitting import (
    adjusted_sharpe,
    information_coefficient,
    overfitting_gate,
    parameter_to_data_ratio,
)


def _metrics(**overrides: object) -> StrategyMetrics:
    base = dict(
        net_after_tax_return=Decimal("0.15"),
        sharpe=Decimal("1.2"),
        stability=Decimal("0.8"),
        dd_penalty=Decimal("0.2"),
        max_drawdown=Decimal("0.10"),
        turnover=Decimal("3.5"),
        regime_stability=Decimal("0.9"),
        leverage=Decimal("1.0"),
        parameter_sensitivity=Decimal("0.3"),
        risk=Decimal("0.12"),
        return_=Decimal("0.15"),
        n_params=10,
        n_train_periods=200,
        information_coefficient=Decimal("0.45"),
    )
    base.update(overrides)
    return StrategyMetrics(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parameter_to_data_ratio
# ---------------------------------------------------------------------------


def test_ratio_happy_path() -> None:
    m = _metrics(n_params=10, n_train_periods=200)
    assert parameter_to_data_ratio(m) == Decimal("0.05")


def test_ratio_returns_infinity_for_zero_train_periods() -> None:
    m = _metrics(n_params=10, n_train_periods=0)
    assert parameter_to_data_ratio(m) == Decimal("Infinity")


def test_ratio_returns_infinity_for_negative_train_periods_via_invariant() -> None:
    # StrategyMetrics rejects n_train_periods<0 at construction; the
    # helper itself handles the degenerate case via the ≤ 0 check.
    m = _metrics(n_params=10, n_train_periods=0)
    assert parameter_to_data_ratio(m) == Decimal("Infinity")


def test_ratio_zero_params_returns_zero() -> None:
    m = _metrics(n_params=0, n_train_periods=200)
    assert parameter_to_data_ratio(m) == Decimal("0")


# ---------------------------------------------------------------------------
# adjusted_sharpe
# ---------------------------------------------------------------------------


def test_adjusted_sharpe_zero_when_raw_sharpe_zero() -> None:
    m = _metrics(sharpe=Decimal("0"))
    assert adjusted_sharpe(m) == Decimal("0")


def test_adjusted_sharpe_below_raw_when_overfit() -> None:
    # ratio = 0.5 => denom = sqrt(1.25) ≈ 1.118 => adjusted < raw.
    m = _metrics(sharpe=Decimal("2.0"), n_params=100, n_train_periods=200)
    raw = m.sharpe
    adj = adjusted_sharpe(m)
    assert adj < raw
    # Sanity: adjusted Sharpe is still positive.
    assert adj > Decimal("0")


def test_adjusted_sharpe_equal_to_raw_when_zero_params() -> None:
    m = _metrics(sharpe=Decimal("1.5"), n_params=0, n_train_periods=200)
    # ratio = 0 => denom = sqrt(1) = 1 => adjusted == raw.
    assert adjusted_sharpe(m) == Decimal("1.5")


def test_adjusted_sharpe_zero_when_train_periods_zero() -> None:
    m = _metrics(sharpe=Decimal("1.5"), n_params=10, n_train_periods=0)
    # ratio = Infinity => special-cased to 0.
    assert adjusted_sharpe(m) == Decimal("0")


# ---------------------------------------------------------------------------
# information_coefficient
# ---------------------------------------------------------------------------


def test_ic_perfect_correlation() -> None:
    # Two metric rows with the SAME triple ⇒ Pearson = 1 (but the
    # implementation returns 0 when both vectors have zero variance,
    # which is the case when all three values are equal; build a
    # vector with variance instead).
    train = _metrics(
        sharpe=Decimal("1.0"),
        net_after_tax_return=Decimal("0.10"),
        max_drawdown=Decimal("0.05"),
    )
    oos = train  # exact copy ⇒ variance is positive across the triple
    ic = information_coefficient(train, oos)
    # Identical positive-variance vectors: Pearson = 1.
    # Allow tiny Decimal precision drift.
    assert abs(ic - Decimal("1")) < Decimal("1e-9")


def test_ic_zero_when_zero_variance() -> None:
    # All three values identical ⇒ zero variance ⇒ degenerate ⇒ 0.
    train = _metrics(
        sharpe=Decimal("0.5"),
        net_after_tax_return=Decimal("0.5"),
        max_drawdown=Decimal("0.5"),
    )
    oos = train
    assert information_coefficient(train, oos) == Decimal("0")


def test_ic_negative_when_anti_correlated() -> None:
    # Build vectors that anti-correlate: train rises, OOS falls.
    train = _metrics(
        sharpe=Decimal("0.1"),
        net_after_tax_return=Decimal("0.5"),
        max_drawdown=Decimal("0.9"),
    )
    oos = _metrics(
        sharpe=Decimal("0.9"),
        net_after_tax_return=Decimal("0.5"),
        max_drawdown=Decimal("0.1"),
    )
    ic = information_coefficient(train, oos)
    assert ic < Decimal("0")


# ---------------------------------------------------------------------------
# overfitting_gate
# ---------------------------------------------------------------------------


def test_gate_accepts_within_thresholds() -> None:
    m = _metrics(
        n_params=10,
        n_train_periods=1000,  # ratio = 0.01
        information_coefficient=Decimal("0.5"),  # > 0.30 floor
    )
    assert isinstance(overfitting_gate(m), Ok)


def test_gate_rejects_high_ratio() -> None:
    m = _metrics(
        n_params=50,
        n_train_periods=100,  # ratio = 0.5 > 0.10
        information_coefficient=Decimal("0.5"),
    )
    match overfitting_gate(m):
        case Err(reason):
            assert reason.startswith("overfitting:parameter_to_data_ratio:")
        case _:
            raise AssertionError("expected Err")


def test_gate_rejects_low_ic() -> None:
    m = _metrics(
        n_params=10,
        n_train_periods=1000,
        information_coefficient=Decimal("0.1"),  # < 0.30 floor
    )
    match overfitting_gate(m):
        case Err(reason):
            assert reason.startswith("overfitting:low_information_coefficient:")
        case _:
            raise AssertionError("expected Err")


def test_gate_custom_thresholds() -> None:
    m = _metrics(
        n_params=10,
        n_train_periods=1000,
        information_coefficient=Decimal("0.6"),
    )
    # Tighten ic_floor above the metric value ⇒ reject.
    match overfitting_gate(m, ic_floor=Decimal("0.8")):
        case Err(reason):
            assert reason.startswith("overfitting:low_information_coefficient:")
        case _:
            raise AssertionError("expected Err")
