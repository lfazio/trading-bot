"""Tests for ``trading_system.tax.config``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.tax.config import TaxConfig


class TestTaxConfig:
    def test_default_matches_yaml(self) -> None:
        # REQ_SDD_CFG_001 / REQ_SDD_CFG_002.
        cfg = TaxConfig.default()
        assert cfg.rate == Decimal("0.30")
        assert cfg.gate_multiplier == 5
        assert cfg.fiscal_year_end_month == 12

    def test_zero_rate_allowed(self) -> None:
        TaxConfig(rate=Decimal(0), gate_multiplier=5)

    def test_full_rate_allowed(self) -> None:
        TaxConfig(rate=Decimal(1), gate_multiplier=5)

    def test_negative_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"TaxConfig\.rate"):
            TaxConfig(rate=Decimal("-0.01"), gate_multiplier=5)

    def test_above_one_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"TaxConfig\.rate"):
            TaxConfig(rate=Decimal("1.01"), gate_multiplier=5)

    @pytest.mark.parametrize("mult", [0, -1])
    def test_non_positive_multiplier_rejected(self, mult: int) -> None:
        with pytest.raises(ValueError, match=r"TaxConfig\.gate_multiplier"):
            TaxConfig(rate=Decimal("0.30"), gate_multiplier=mult)

    @pytest.mark.parametrize("month", [0, 13])
    def test_invalid_month_rejected(self, month: int) -> None:
        with pytest.raises(ValueError, match=r"TaxConfig\.fiscal_year_end_month"):
            TaxConfig(rate=Decimal("0.30"), gate_multiplier=5, fiscal_year_end_month=month)

    def test_frozen(self) -> None:
        cfg = TaxConfig.default()
        with pytest.raises(AttributeError):
            cfg.rate = Decimal("0.50")  # type: ignore[misc]
