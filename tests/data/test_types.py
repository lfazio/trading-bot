"""Tests for ``trading_system.data.types``."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.data.types import Bar, Fundamentals, Timeframe, timeframe_delta
from trading_system.models.money import Currency, Money

EUR = Currency.EUR


class TestTimeframe:
    def test_strenum_values(self) -> None:
        assert Timeframe.M1.value == "1m"
        assert Timeframe.D1.value == "1d"

    def test_membership(self) -> None:
        assert {tf for tf in Timeframe} == {Timeframe.M1, Timeframe.M5, Timeframe.H1, Timeframe.D1}


class TestTimeframeDelta:
    def test_minute_delta(self) -> None:
        assert timeframe_delta(Timeframe.M1) == timedelta(minutes=1)

    def test_hour_delta(self) -> None:
        assert timeframe_delta(Timeframe.H1) == timedelta(hours=1)

    def test_day_delta(self) -> None:
        assert timeframe_delta(Timeframe.D1) == timedelta(days=1)


def make_bar(**overrides: object) -> Bar:
    base: dict[str, object] = {
        "at": datetime(2026, 5, 1, 9, 30),
        "open": Decimal("100.00"),
        "high": Decimal("101.00"),
        "low": Decimal("99.00"),
        "close": Decimal("100.50"),
        "volume": Decimal("12345"),
    }
    base.update(overrides)
    return Bar(**base)  # type: ignore[arg-type]


class TestBar:
    def test_valid(self) -> None:
        b = make_bar()
        assert b.close == Decimal("100.50")

    @pytest.mark.parametrize("field", ["open", "high", "low", "close"])
    def test_non_positive_price_rejected(self, field: str) -> None:
        with pytest.raises(ValueError, match=rf"Bar\.{field} must be > 0"):
            make_bar(**{field: Decimal(0)})

    def test_negative_volume_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"Bar\.volume must be >= 0"):
            make_bar(volume=Decimal("-1"))

    def test_zero_volume_allowed(self) -> None:
        make_bar(volume=Decimal(0))

    def test_high_below_max_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"Bar\.high.*must be >= max"):
            make_bar(open=Decimal("100"), close=Decimal("105"), high=Decimal("104"))

    def test_low_above_min_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"Bar\.low.*must be <= min"):
            make_bar(open=Decimal("100"), close=Decimal("95"), low=Decimal("96"))


def make_fund(**overrides: object) -> Fundamentals:
    base: dict[str, object] = {
        "yield_": Decimal("0.045"),
        "payout_ratio": Decimal("0.55"),
        "free_cash_flow": Money(Decimal(1000), EUR),
        "debt_equity": Decimal("0.8"),
        "dividend_history_years": 10,
    }
    base.update(overrides)
    return Fundamentals(**base)  # type: ignore[arg-type]


class TestFundamentals:
    def test_valid(self) -> None:
        f = make_fund()
        assert f.yield_ == Decimal("0.045")

    def test_zero_yield_allowed(self) -> None:
        make_fund(yield_=Decimal(0))

    def test_negative_yield_rejected(self) -> None:
        with pytest.raises(ValueError, match="yield_"):
            make_fund(yield_=Decimal("-0.01"))

    def test_negative_payout_rejected(self) -> None:
        with pytest.raises(ValueError, match="payout_ratio"):
            make_fund(payout_ratio=Decimal("-0.1"))

    def test_negative_de_rejected(self) -> None:
        with pytest.raises(ValueError, match="debt_equity"):
            make_fund(debt_equity=Decimal("-0.5"))

    def test_negative_history_rejected(self) -> None:
        with pytest.raises(ValueError, match="dividend_history_years"):
            make_fund(dividend_history_years=-1)
