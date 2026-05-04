"""Tests for ``trading_system.risk.metrics``."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from trading_system.models.flow import EquityPoint
from trading_system.models.money import Currency, Money
from trading_system.risk.metrics import (
    drawdown_now,
    portfolio_vol_ann,
    realized_correlation,
)

EUR = Currency.EUR


def equity_curve(values: list[str], *, start: datetime | None = None) -> list[EquityPoint]:
    s = start or datetime(2026, 1, 1)
    out: list[EquityPoint] = []
    peak = Decimal(0)
    for i, v in enumerate(values):
        amount = Decimal(v)
        peak = max(peak, amount)
        dd = Decimal(0) if peak <= 0 else max(Decimal(0), Decimal(1) - amount / peak)
        out.append(
            EquityPoint(
                at=s + timedelta(days=i),
                equity_gross=Money(amount, EUR),
                equity_after_tax=Money(amount, EUR),
                drawdown_pct=dd,
            )
        )
    return out


# ---------------------------------------------------------------------------
# drawdown_now
# ---------------------------------------------------------------------------


class TestDrawdownNow:
    def test_empty_curve_zero(self) -> None:
        assert drawdown_now([]) == Decimal(0)

    def test_no_drawdown_at_peak(self) -> None:
        curve = equity_curve(["100", "110", "120"])
        assert drawdown_now(curve) == Decimal(0)

    def test_drawdown_from_peak(self) -> None:
        # peak = 120, current = 90 -> 1 - 90/120 = 0.25.
        curve = equity_curve(["100", "120", "90"])
        assert drawdown_now(curve) == Decimal("0.25")

    def test_zero_peak_returns_zero(self) -> None:
        curve = equity_curve(["0", "0", "0"])
        assert drawdown_now(curve) == Decimal(0)

    def test_clamps_negative_to_zero(self) -> None:
        # Should never happen via construction (peak is max), but the
        # branch still guards against pathological inputs.
        curve = equity_curve(["100", "120"])
        assert drawdown_now(curve) == Decimal(0)


# ---------------------------------------------------------------------------
# portfolio_vol_ann
# ---------------------------------------------------------------------------


class TestPortfolioVolAnn:
    def test_constant_curve_zero_vol(self) -> None:
        curve = equity_curve(["100"] * 31)
        assert portfolio_vol_ann(curve, 30) == Decimal(0)

    def test_insufficient_data_returns_none(self) -> None:
        curve = equity_curve(["100"] * 30)  # one short
        assert portfolio_vol_ann(curve, 30) is None

    def test_zero_window_returns_none(self) -> None:
        curve = equity_curve(["100"] * 31)
        assert portfolio_vol_ann(curve, 0) is None

    def test_oscillating_curve_positive_vol(self) -> None:
        values = ["100"]
        for i in range(60):
            prev = Decimal(values[-1])
            if i % 2 == 0:
                values.append(str(prev * Decimal("1.01")))
            else:
                values.append(str(prev * Decimal("0.99")))
        curve = equity_curve(values)
        vol = portfolio_vol_ann(curve, 30)
        assert vol is not None
        assert vol > Decimal(0)

    def test_zero_equity_in_window_returns_none(self) -> None:
        # Embed a zero equity in the lookback window.
        values = ["100"] * 30 + ["0", "100"]
        curve = equity_curve(values)
        # Window=2 -> uses the last 2 returns; the zero -> 100 transition
        # has prev=0 -> guard returns None.
        assert portfolio_vol_ann(curve, 2) is None


# ---------------------------------------------------------------------------
# realized_correlation
# ---------------------------------------------------------------------------


class TestRealizedCorrelation:
    def test_identical_series_correlation_one(self) -> None:
        a = [Decimal(i) for i in range(1, 11)]
        b = list(a)
        assert realized_correlation(a, b) == Decimal(1)

    def test_perfectly_anti_correlated(self) -> None:
        a = [Decimal(i) for i in range(1, 11)]
        b = [Decimal(-i) for i in range(1, 11)]
        c = realized_correlation(a, b)
        assert c is not None
        # Allow tiny rounding tolerance — we expect exactly -1 here.
        assert abs(c - Decimal(-1)) < Decimal("1e-9")

    def test_constant_series_returns_none(self) -> None:
        # var_a = 0 -> degenerate.
        a = [Decimal(1)] * 10
        b = [Decimal(i) for i in range(1, 11)]
        assert realized_correlation(a, b) is None

    def test_mismatched_lengths_returns_none(self) -> None:
        a = [Decimal(1), Decimal(2)]
        b = [Decimal(1), Decimal(2), Decimal(3)]
        assert realized_correlation(a, b) is None

    def test_too_short_returns_none(self) -> None:
        a = [Decimal(1)]
        b = [Decimal(2)]
        assert realized_correlation(a, b) is None
