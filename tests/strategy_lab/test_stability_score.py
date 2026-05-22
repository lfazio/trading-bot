"""Tests for ``compute_stability_score`` — REQ_SDD_ALG_003.

REQ_SDD_ALG_003 — strategy stability score SHALL be a 12-month
rolling Sharpe with ≥ 100 observations; below the observation
floor the score SHALL be ``None`` and the candidate SHALL be
rejected as immature.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.models.flow import EquityPoint
from trading_system.models.money import Currency, Money
from trading_system.strategy_lab.scoring import (
    MIN_OBSERVATIONS_FOR_STABILITY,
    compute_stability_score,
)


def _eq(amount: str, day: int) -> EquityPoint:
    money = Money(amount=Decimal(amount), currency=Currency.EUR)
    return EquityPoint(
        at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day),
        equity_gross=money,
        equity_after_tax=money,
        drawdown_pct=Decimal(0),
    )


def test_below_observation_floor_returns_none() -> None:
    """REQ_SDD_ALG_003 — fewer than 100 returns (101 curve points)
    SHALL produce ``None`` so the candidate is rejected as
    immature."""
    curve = [_eq(str(10000 + i), i) for i in range(50)]
    assert compute_stability_score(curve) is None


def test_exact_floor_minus_one_returns_none() -> None:
    """REQ_SDD_ALG_003 — exactly ``min_observations`` curve points
    (min_observations - 1 returns) is still below the floor."""
    curve = [_eq(str(10000 + i), i) for i in range(MIN_OBSERVATIONS_FOR_STABILITY)]
    assert compute_stability_score(curve) is None


def test_at_floor_returns_decimal() -> None:
    """REQ_SDD_ALG_003 — exactly ``min_observations + 1`` curve
    points (min_observations returns) is the smallest acceptable
    input — the score is a Decimal, not None."""
    # Use a deterministic upward-trending curve with non-zero variance.
    curve = []
    base = Decimal("10000")
    for i in range(MIN_OBSERVATIONS_FOR_STABILITY + 1):
        # alternating +0.5% / +0.6% per step — non-zero variance
        delta = Decimal("0.005") if i % 2 == 0 else Decimal("0.006")
        base = base * (Decimal(1) + delta)
        money = Money(amount=base, currency=Currency.EUR)
        curve.append(
            EquityPoint(
                at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i),
                equity_gross=money,
                equity_after_tax=money,
                drawdown_pct=Decimal(0),
            )
        )
    result = compute_stability_score(curve)
    assert isinstance(result, Decimal)
    assert result > 0  # upward trend → positive Sharpe


def test_zero_variance_returns_zero() -> None:
    """A flat equity curve has zero variance — the score SHALL
    be ``Decimal(0)`` rather than panic with a division-by-zero."""
    curve = [_eq("10000", i) for i in range(MIN_OBSERVATIONS_FOR_STABILITY + 1)]
    result = compute_stability_score(curve)
    assert result == Decimal(0)


def test_custom_observation_floor() -> None:
    """Operators MAY override ``min_observations`` for sub-daily
    bars; passing a lower floor SHALL accept correspondingly
    shorter curves."""
    curve = [_eq(str(10000 + i), i) for i in range(50)]
    # 50 points = 49 returns; default floor would return None,
    # but min_observations=20 accepts.
    result = compute_stability_score(curve, min_observations=20)
    assert result is not None


def test_zero_equity_short_circuits_to_none() -> None:
    """If the equity series ever reaches zero, the return-rate
    division is undefined; the function SHALL return ``None``
    rather than panic."""
    curve = [_eq("10000", 0)] + [
        _eq(str(10000 + i), i) for i in range(1, 50)
    ]
    # Insert a zero-equity point partway through.
    zero = Money(amount=Decimal(0), currency=Currency.EUR)
    curve[25] = EquityPoint(
        at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=25),
        equity_gross=zero,
        equity_after_tax=zero,
        drawdown_pct=Decimal(0),
    )
    result = compute_stability_score(curve, min_observations=40)
    assert result is None


def test_determinism() -> None:
    """REQ_NF_REP_001 family — same input SHALL produce equal
    output across two independent invocations."""
    curve = []
    base = Decimal("10000")
    for i in range(MIN_OBSERVATIONS_FOR_STABILITY + 1):
        delta = Decimal("0.004") if i % 3 == 0 else Decimal("0.002")
        base = base * (Decimal(1) + delta)
        money = Money(amount=base, currency=Currency.EUR)
        curve.append(
            EquityPoint(
                at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i),
                equity_gross=money,
                equity_after_tax=money,
                drawdown_pct=Decimal(0),
            )
        )
    a = compute_stability_score(curve)
    b = compute_stability_score(curve)
    assert a == b
