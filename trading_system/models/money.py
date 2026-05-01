"""Money and currency types.

``Money`` is a ``Decimal``-backed monetary value tagged with a
``Currency``. Cross-currency arithmetic is a programmer error and
panics via ``AssertionError`` (REQ_SDD_ERR_001 — panic on programmer
invariants). Construction-time validation rejects NaN / infinity
(``raise ValueError``).

REQ refs: REQ_SDD_TYP_001 (Decimal everywhere), REQ_SDD_TYP_003
(``Currency`` as ``StrEnum``), REQ_F_TAX_001 / REQ_F_TAX_002
(net-of-tax math operates on ``Money``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Currency(StrEnum):
    """ISO-4217 currency codes used by the system."""

    EUR = "EUR"
    USD = "USD"
    GBP = "GBP"
    CHF = "CHF"


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """Monetary value tagged with a currency.

    All arithmetic preserves the currency tag and panics on cross-
    currency operations. Magnitude operations (``__abs__``, unary
    ``__neg__``) keep the tag.
    """

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if self.amount.is_nan():
            raise ValueError("Money.amount must not be NaN")
        if self.amount.is_infinite():
            raise ValueError("Money.amount must be finite")

    # ------------------------------------------------------------------
    # Arithmetic
    # ------------------------------------------------------------------

    def __add__(self, other: Money) -> Money:
        assert self.currency == other.currency, (
            f"cross-currency add: {self.currency} vs {other.currency}"
        )
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        assert self.currency == other.currency, (
            f"cross-currency sub: {self.currency} vs {other.currency}"
        )
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, k: Decimal | int) -> Money:
        return Money(self.amount * Decimal(k), self.currency)

    def __rmul__(self, k: Decimal | int) -> Money:
        return self.__mul__(k)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    # ------------------------------------------------------------------
    # Comparisons (currency-tagged)
    # ------------------------------------------------------------------

    def __lt__(self, other: Money) -> bool:
        assert self.currency == other.currency, (
            f"cross-currency compare: {self.currency} vs {other.currency}"
        )
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        assert self.currency == other.currency
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        assert self.currency == other.currency
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        assert self.currency == other.currency
        return self.amount >= other.amount

    # Equality / hash come for free from frozen dataclass; they include
    # both fields, so ``Money(1, EUR) != Money(1, USD)``.
