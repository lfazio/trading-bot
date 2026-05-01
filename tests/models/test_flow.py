"""Tests for ``trading_system.models.flow``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.models.flow import EquityPoint, Injection
from trading_system.models.money import Currency, Money

EUR = Currency.EUR
USD = Currency.USD


class TestInjection:
    def test_valid(self) -> None:
        i = Injection(amount=Money(Decimal(1000), EUR), at=datetime(2026, 5, 1), source="DCA")
        assert i.source == "DCA"

    def test_default_source(self) -> None:
        i = Injection(amount=Money(Decimal(1), EUR), at=datetime(2026, 5, 1))
        assert i.source == ""

    def test_zero_amount_rejected(self) -> None:
        with pytest.raises(ValueError, match="amount must be > 0"):
            Injection(amount=Money(Decimal(0), EUR), at=datetime(2026, 5, 1))

    def test_negative_amount_rejected(self) -> None:
        with pytest.raises(ValueError, match="amount must be > 0"):
            Injection(amount=Money(Decimal("-100"), EUR), at=datetime(2026, 5, 1))


class TestEquityPoint:
    def test_valid(self) -> None:
        p = EquityPoint(
            at=datetime(2026, 5, 1),
            equity_gross=Money(Decimal(1000), EUR),
            equity_after_tax=Money(Decimal(700), EUR),
            drawdown_pct=Decimal("0.10"),
        )
        assert p.drawdown_pct == Decimal("0.10")

    def test_zero_drawdown_allowed(self) -> None:
        EquityPoint(
            at=datetime(2026, 5, 1),
            equity_gross=Money(Decimal(1000), EUR),
            equity_after_tax=Money(Decimal(700), EUR),
            drawdown_pct=Decimal(0),
        )

    def test_full_drawdown_allowed(self) -> None:
        EquityPoint(
            at=datetime(2026, 5, 1),
            equity_gross=Money(Decimal(1000), EUR),
            equity_after_tax=Money(Decimal(700), EUR),
            drawdown_pct=Decimal(1),
        )

    def test_currency_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="must share a currency"):
            EquityPoint(
                at=datetime(2026, 5, 1),
                equity_gross=Money(Decimal(1000), EUR),
                equity_after_tax=Money(Decimal(700), USD),
                drawdown_pct=Decimal(0),
            )

    @pytest.mark.parametrize("dd", [Decimal("-0.01"), Decimal("1.01")])
    def test_drawdown_out_of_range_rejected(self, dd: Decimal) -> None:
        with pytest.raises(ValueError, match="drawdown_pct"):
            EquityPoint(
                at=datetime(2026, 5, 1),
                equity_gross=Money(Decimal(1000), EUR),
                equity_after_tax=Money(Decimal(700), EUR),
                drawdown_pct=dd,
            )
