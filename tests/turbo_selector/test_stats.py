"""Tests for ``trading_system.turbo_selector.stats``."""

from __future__ import annotations

from decimal import Decimal

from trading_system.turbo_selector.stats import (
    TRADING_DAYS_PER_YEAR,
    avg_volume,
    realized_vol,
)


class TestRealizedVol:
    def test_constant_series_zero_vol(self) -> None:
        # No variation => zero variance => zero vol.
        closes = [Decimal(100)] * 31
        assert realized_vol(closes, 30) == Decimal(0)

    def test_insufficient_data_returns_none(self) -> None:
        # Need window + 1 closes.
        assert realized_vol([Decimal(100)] * 30, 30) is None

    def test_zero_or_negative_close_returns_none(self) -> None:
        closes = [Decimal(100)] * 30 + [Decimal(0), Decimal(100)]
        # Reference close in window is zero -> guard returns None.
        assert realized_vol(closes, 5) is None

    def test_positive_for_oscillating_series(self) -> None:
        # Alternating +5% / -5% returns -> visible vol.
        closes = [Decimal(100)]
        for i in range(60):
            prev = closes[-1]
            if i % 2 == 0:
                closes.append(prev * Decimal("1.05"))
            else:
                closes.append(prev * Decimal("0.95"))
        vol = realized_vol(closes, 30)
        assert vol is not None
        assert vol > Decimal(0)

    def test_window_zero_returns_none(self) -> None:
        assert realized_vol([Decimal(100)] * 5, 0) is None

    def test_annualization_factor(self) -> None:
        # The annualizer is sqrt(252). Verify the constant is exactly that.
        actual = TRADING_DAYS_PER_YEAR
        assert actual == Decimal(252)


class TestAvgVolume:
    def test_basic(self) -> None:
        volumes = [Decimal(100), Decimal(200), Decimal(300)]
        assert avg_volume(volumes, 3) == Decimal(200)

    def test_uses_only_tail_window(self) -> None:
        volumes = [Decimal(1)] * 100 + [Decimal(50), Decimal(50)]
        # window=2 -> last two entries.
        assert avg_volume(volumes, 2) == Decimal(50)

    def test_insufficient_data(self) -> None:
        assert avg_volume([Decimal(1)] * 5, 10) is None

    def test_zero_window(self) -> None:
        assert avg_volume([Decimal(1)] * 5, 0) is None
