"""Tests for ``trading_system.safety.anomaly``."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from trading_system.models.flow import EquityPoint
from trading_system.models.money import Currency, Money
from trading_system.safety.anomaly import (
    rapid_decline_breach,
    single_day_loss_breach,
)

EUR = Currency.EUR


def equity_curve(values: list[str]) -> list[EquityPoint]:
    s = datetime(2026, 5, 1)
    out: list[EquityPoint] = []
    peak = Decimal(0)
    for i, v in enumerate(values):
        amt = Decimal(v)
        peak = max(peak, amt)
        dd = max(Decimal(0), Decimal(1) - amt / peak) if peak > 0 else Decimal(0)
        out.append(
            EquityPoint(
                at=s + timedelta(days=i),
                equity_gross=Money(amt, EUR),
                equity_after_tax=Money(amt, EUR),
                drawdown_pct=dd,
            )
        )
    return out


# ---------------------------------------------------------------------------
# single_day_loss_breach
# ---------------------------------------------------------------------------


class TestSingleDayLoss:
    def test_below_threshold_no_breach(self) -> None:
        # 1% loss; threshold 5%.
        curve = equity_curve(["100", "99"])
        assert single_day_loss_breach(curve, Decimal("0.05")) is False

    def test_above_threshold_breach(self) -> None:
        # 6% loss; threshold 5%.
        curve = equity_curve(["100", "94"])
        assert single_day_loss_breach(curve, Decimal("0.05")) is True

    def test_at_threshold_no_breach(self) -> None:
        # Strictly greater-than (not >=). 5% loss exactly: no breach.
        curve = equity_curve(["100", "95"])
        assert single_day_loss_breach(curve, Decimal("0.05")) is False

    def test_gain_no_breach(self) -> None:
        curve = equity_curve(["100", "110"])
        assert single_day_loss_breach(curve, Decimal("0.05")) is False

    def test_short_curve_returns_false(self) -> None:
        assert single_day_loss_breach(equity_curve(["100"]), Decimal("0.05")) is False
        assert single_day_loss_breach(equity_curve([]), Decimal("0.05")) is False

    def test_zero_threshold_returns_false(self) -> None:
        curve = equity_curve(["100", "50"])
        assert single_day_loss_breach(curve, Decimal(0)) is False

    def test_zero_prev_equity_returns_false(self) -> None:
        curve = equity_curve(["0", "100"])
        # prev=0 -> guard returns False.
        assert single_day_loss_breach(curve, Decimal("0.05")) is False


# ---------------------------------------------------------------------------
# rapid_decline_breach
# ---------------------------------------------------------------------------


class TestRapidDecline:
    def test_default_5d_breach(self) -> None:
        # 100 -> 80 over 5 steps = 20% decline; threshold 10%.
        curve = equity_curve(["100", "98", "96", "92", "85", "80"])
        assert rapid_decline_breach(curve, days=5, pct=Decimal("0.10")) is True

    def test_below_threshold_no_breach(self) -> None:
        # 100 -> 95 over 5 steps = 5%; threshold 10%.
        curve = equity_curve(["100", "99", "98", "97", "96", "95"])
        assert rapid_decline_breach(curve, days=5, pct=Decimal("0.10")) is False

    def test_short_curve_returns_false(self) -> None:
        # Need len > days; 5 points + days=5 is not enough.
        curve = equity_curve(["100", "98", "96", "92", "85"])
        assert rapid_decline_breach(curve, days=5, pct=Decimal("0.10")) is False

    def test_zero_or_negative_inputs_return_false(self) -> None:
        curve = equity_curve(["100"] * 10 + ["50"])
        assert rapid_decline_breach(curve, days=0, pct=Decimal("0.10")) is False
        assert rapid_decline_breach(curve, days=5, pct=Decimal(0)) is False

    def test_zero_anchor_returns_false(self) -> None:
        # First point is 0; the anchor is the point at len-(days+1).
        curve = equity_curve(["0"] + ["100"] * 5)
        assert rapid_decline_breach(curve, days=5, pct=Decimal("0.10")) is False
