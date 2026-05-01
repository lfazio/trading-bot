"""Tests for ``trading_system.models.money``.

Verifies REQ_SDD_TYP_001 (Decimal everywhere; no float),
REQ_SDD_TYP_003 (Currency as StrEnum), and the cross-currency panic
discipline (REQ_SDD_ERR_001).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.money import Currency, Money

EUR = Currency.EUR
USD = Currency.USD


class TestCurrency:
    def test_strenum_values(self) -> None:
        assert Currency.EUR.value == "EUR"
        assert Currency.USD.value == "USD"

    def test_membership(self) -> None:
        assert Currency("EUR") is Currency.EUR
        with pytest.raises(ValueError):
            Currency("XYZ")


class TestMoneyConstruction:
    def test_basic(self) -> None:
        m = Money(Decimal("100.50"), EUR)
        assert m.amount == Decimal("100.50")
        assert m.currency is EUR

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be NaN"):
            Money(Decimal("NaN"), EUR)

    def test_infinity_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be finite"):
            Money(Decimal("Infinity"), EUR)
        with pytest.raises(ValueError, match="must be finite"):
            Money(Decimal("-Infinity"), EUR)

    def test_frozen(self) -> None:
        m = Money(Decimal(1), EUR)
        with pytest.raises(AttributeError):
            m.amount = Decimal(2)  # type: ignore[misc]


class TestMoneyArithmetic:
    def test_add_same_currency(self) -> None:
        result = Money(Decimal(10), EUR) + Money(Decimal(5), EUR)
        assert result == Money(Decimal(15), EUR)

    def test_add_cross_currency_panics(self) -> None:
        with pytest.raises(AssertionError, match="cross-currency add"):
            Money(Decimal(10), EUR) + Money(Decimal(5), USD)

    def test_sub_same_currency(self) -> None:
        result = Money(Decimal(10), EUR) - Money(Decimal(3), EUR)
        assert result == Money(Decimal(7), EUR)

    def test_sub_cross_currency_panics(self) -> None:
        with pytest.raises(AssertionError, match="cross-currency sub"):
            Money(Decimal(10), EUR) - Money(Decimal(3), USD)

    def test_mul_int(self) -> None:
        assert Money(Decimal("10.5"), EUR) * 2 == Money(Decimal("21.0"), EUR)

    def test_mul_decimal(self) -> None:
        assert Money(Decimal(10), EUR) * Decimal("0.7") == Money(Decimal("7.0"), EUR)

    def test_rmul(self) -> None:
        assert 2 * Money(Decimal(5), EUR) == Money(Decimal(10), EUR)

    def test_neg(self) -> None:
        assert -Money(Decimal(5), EUR) == Money(Decimal(-5), EUR)

    def test_abs(self) -> None:
        assert abs(Money(Decimal(-5), EUR)) == Money(Decimal(5), EUR)


class TestMoneyComparison:
    def test_lt(self) -> None:
        assert Money(Decimal(1), EUR) < Money(Decimal(2), EUR)
        assert not (Money(Decimal(2), EUR) < Money(Decimal(1), EUR))

    def test_le(self) -> None:
        assert Money(Decimal(1), EUR) <= Money(Decimal(1), EUR)

    def test_gt(self) -> None:
        assert Money(Decimal(2), EUR) > Money(Decimal(1), EUR)

    def test_ge(self) -> None:
        assert Money(Decimal(2), EUR) >= Money(Decimal(2), EUR)

    def test_compare_cross_currency_panics(self) -> None:
        with pytest.raises(AssertionError, match="cross-currency compare"):
            _ = Money(Decimal(1), EUR) < Money(Decimal(1), USD)


class TestMoneyEquality:
    def test_equal_same_amount_currency(self) -> None:
        assert Money(Decimal(1), EUR) == Money(Decimal(1), EUR)

    def test_different_currency_not_equal(self) -> None:
        # Equality is field-wise; cross-currency *equality* doesn't
        # panic because comparing for equality is allowed (the dataclass
        # default __eq__ short-circuits on field difference).
        assert Money(Decimal(1), EUR) != Money(Decimal(1), USD)

    def test_different_amount_not_equal(self) -> None:
        assert Money(Decimal(1), EUR) != Money(Decimal(2), EUR)
