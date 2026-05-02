"""Tests for ``trading_system.tax.harvest`` (REQ_F_TAX_006)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.models.money import Currency, Money
from trading_system.tax.config import TaxConfig
from trading_system.tax.harvest import (
    HarvestSuggestion,
    Realization,
    fiscal_year_of,
    harvest_losses,
)

EUR = Currency.EUR


def cfg_calendar() -> TaxConfig:
    return TaxConfig(rate=Decimal("0.30"), gate_multiplier=5, fiscal_year_end_month=12)


def cfg_march() -> TaxConfig:
    """End-of-March fiscal year (April → March cycle)."""
    return TaxConfig(rate=Decimal("0.30"), gate_multiplier=5, fiscal_year_end_month=3)


def loss(pid: str, year: int, month: int, amount: str) -> Realization:
    return Realization(
        position_id=pid,
        realized_at=datetime(year, month, 15),
        gross=Money(Decimal(amount), EUR),
    )


# ---------------------------------------------------------------------------
# Realization / HarvestSuggestion construction
# ---------------------------------------------------------------------------


class TestRealization:
    def test_basic(self) -> None:
        r = Realization(
            position_id="p1", realized_at=datetime(2026, 5, 1), gross=Money(Decimal("10"), EUR)
        )
        assert r.position_id == "p1"

    def test_empty_position_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="position_id must be non-empty"):
            Realization(
                position_id="",
                realized_at=datetime(2026, 5, 1),
                gross=Money(Decimal("10"), EUR),
            )

    def test_loss_allowed(self) -> None:
        Realization(
            position_id="p1",
            realized_at=datetime(2026, 5, 1),
            gross=Money(Decimal("-50"), EUR),
        )


class TestHarvestSuggestion:
    def test_basic(self) -> None:
        s = HarvestSuggestion(position_id="p1", loss_magnitude=Money(Decimal(50), EUR))
        assert s.loss_magnitude.amount == Decimal(50)

    def test_negative_magnitude_rejected(self) -> None:
        with pytest.raises(ValueError, match="loss_magnitude must be >= 0"):
            HarvestSuggestion(position_id="p1", loss_magnitude=Money(Decimal("-1"), EUR))


# ---------------------------------------------------------------------------
# fiscal_year_of
# ---------------------------------------------------------------------------


class TestFiscalYear:
    def test_calendar_year(self) -> None:
        assert fiscal_year_of(datetime(2026, 1, 1), cfg_calendar()) == 2026
        assert fiscal_year_of(datetime(2026, 12, 31), cfg_calendar()) == 2026

    def test_march_end_year_after_march(self) -> None:
        # April 2025 falls into FY 2026 (FY 2026 = April 2025 → March 2026).
        assert fiscal_year_of(datetime(2025, 4, 1), cfg_march()) == 2026

    def test_march_end_year_before_or_at_march(self) -> None:
        # March 2026 still belongs to FY 2026.
        assert fiscal_year_of(datetime(2026, 3, 31), cfg_march()) == 2026


# ---------------------------------------------------------------------------
# harvest_losses
# ---------------------------------------------------------------------------


class TestHarvestLosses:
    def test_empty_when_no_gains(self) -> None:
        ledger = [loss("p1", 2026, 5, "-100")]
        result = harvest_losses(
            cfg_calendar(),
            ledger,
            fiscal_year=2026,
            capital_gains_so_far=Money(Decimal(0), EUR),
        )
        assert result == []

    def test_empty_when_negative_gains(self) -> None:
        # Gains_so_far <= 0: nothing to offset.
        ledger = [loss("p1", 2026, 5, "-100")]
        result = harvest_losses(
            cfg_calendar(),
            ledger,
            fiscal_year=2026,
            capital_gains_so_far=Money(Decimal("-50"), EUR),
        )
        assert result == []

    def test_empty_when_no_losses(self) -> None:
        ledger = [loss("p1", 2026, 5, "100")]  # a gain
        result = harvest_losses(
            cfg_calendar(),
            ledger,
            fiscal_year=2026,
            capital_gains_so_far=Money(Decimal(50), EUR),
        )
        assert result == []

    def test_filters_out_other_fiscal_years(self) -> None:
        ledger = [
            loss("p_old", 2025, 5, "-200"),
            loss("p_now", 2026, 5, "-50"),
        ]
        result = harvest_losses(
            cfg_calendar(),
            ledger,
            fiscal_year=2026,
            capital_gains_so_far=Money(Decimal(100), EUR),
        )
        assert [s.position_id for s in result] == ["p_now"]

    def test_picks_largest_loss_first(self) -> None:
        ledger = [
            loss("small", 2026, 5, "-30"),
            loss("big", 2026, 5, "-200"),
            loss("mid", 2026, 5, "-80"),
        ]
        result = harvest_losses(
            cfg_calendar(),
            ledger,
            fiscal_year=2026,
            capital_gains_so_far=Money(Decimal(100), EUR),
        )
        # -200 alone covers 100 in gains; only one suggestion needed.
        assert [s.position_id for s in result] == ["big"]
        assert result[0].loss_magnitude == Money(Decimal(200), EUR)

    def test_accumulates_until_offset(self) -> None:
        ledger = [
            loss("a", 2026, 5, "-30"),
            loss("b", 2026, 5, "-40"),
            loss("c", 2026, 5, "-20"),
        ]
        result = harvest_losses(
            cfg_calendar(),
            ledger,
            fiscal_year=2026,
            capital_gains_so_far=Money(Decimal(60), EUR),
        )
        # Largest first: -40 then -30; -20 not needed (40 + 30 ≥ 60).
        assert [s.position_id for s in result] == ["b", "a"]

    def test_takes_all_losses_if_insufficient(self) -> None:
        ledger = [
            loss("a", 2026, 5, "-30"),
            loss("b", 2026, 5, "-20"),
        ]
        result = harvest_losses(
            cfg_calendar(),
            ledger,
            fiscal_year=2026,
            capital_gains_so_far=Money(Decimal(1000), EUR),
        )
        # Only 50 of losses available, but caller still gets both suggestions.
        assert {s.position_id for s in result} == {"a", "b"}

    def test_returns_positive_loss_magnitude(self) -> None:
        ledger = [loss("p", 2026, 5, "-150")]
        result = harvest_losses(
            cfg_calendar(),
            ledger,
            fiscal_year=2026,
            capital_gains_so_far=Money(Decimal(50), EUR),
        )
        assert result[0].loss_magnitude == Money(Decimal(150), EUR)

    def test_cross_currency_panics(self) -> None:
        ledger = [
            Realization(
                position_id="p",
                realized_at=datetime(2026, 5, 1),
                gross=Money(Decimal("-100"), Currency.USD),
            )
        ]
        with pytest.raises(AssertionError, match="cross-currency"):
            harvest_losses(
                cfg_calendar(),
                ledger,
                fiscal_year=2026,
                capital_gains_so_far=Money(Decimal(50), EUR),
            )

    def test_march_end_fiscal_isolation(self) -> None:
        # Loss in March 2026 belongs to FY 2026; loss in April 2026 to FY 2027.
        ledger = [
            loss("march_loss", 2026, 3, "-100"),
            loss("april_loss", 2026, 4, "-200"),
        ]
        result = harvest_losses(
            cfg_march(),
            ledger,
            fiscal_year=2026,
            capital_gains_so_far=Money(Decimal(50), EUR),
        )
        assert [s.position_id for s in result] == ["march_loss"]
