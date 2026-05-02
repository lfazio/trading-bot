"""Tests for ``trading_system.screener.config``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.screener.config import ScreenerConfig


class TestScreenerConfig:
    def test_defaults(self) -> None:
        cfg = ScreenerConfig()
        assert cfg.yield_min == Decimal("0.03")
        assert cfg.yield_max == Decimal("0.07")
        assert cfg.payout_max == Decimal("0.70")
        assert cfg.debt_equity_max == Decimal("1.5")
        assert cfg.min_history_years == 5
        assert cfg.weights == (Decimal("0.5"), Decimal("0.3"), Decimal("0.2"))
        assert cfg.stability_full_years == 20

    def test_yield_min_above_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="yield_min <= yield_max"):
            ScreenerConfig(yield_min=Decimal("0.08"), yield_max=Decimal("0.05"))

    def test_negative_yield_min_rejected(self) -> None:
        with pytest.raises(ValueError, match="yield_min"):
            ScreenerConfig(yield_min=Decimal("-0.01"))

    def test_yield_max_above_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="yield_max"):
            ScreenerConfig(yield_max=Decimal("1.01"))

    @pytest.mark.parametrize("p", [Decimal(0), Decimal("1.01"), Decimal("-0.1")])
    def test_invalid_payout_max_rejected(self, p: Decimal) -> None:
        with pytest.raises(ValueError, match="payout_max"):
            ScreenerConfig(payout_max=p)

    def test_zero_de_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="debt_equity_max"):
            ScreenerConfig(debt_equity_max=Decimal(0))

    def test_negative_history_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_history_years"):
            ScreenerConfig(min_history_years=-1)

    def test_zero_full_years_rejected(self) -> None:
        with pytest.raises(ValueError, match="stability_full_years"):
            ScreenerConfig(stability_full_years=0)

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValueError, match="weights must all be"):
            ScreenerConfig(weights=(Decimal("-0.1"), Decimal("0.5"), Decimal("0.6")))

    def test_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match="weights must sum to 1"):
            ScreenerConfig(weights=(Decimal("0.5"), Decimal("0.3"), Decimal("0.1")))

    def test_frozen(self) -> None:
        cfg = ScreenerConfig()
        with pytest.raises(AttributeError):
            cfg.yield_min = Decimal("0.05")  # type: ignore[misc]
