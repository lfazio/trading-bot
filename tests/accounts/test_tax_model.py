"""Tests for ``trading_system.accounts.tax_model``.

Covers TC_ACC_004 (TaxModel Protocol conformance; France CTO default
0.30; losses pass through pre-tax; second model admissible without
touching `tax/`'s code).

REQ refs: REQ_F_ACC_005, REQ_C_TAX_001, REQ_SDS_ACC_003,
REQ_SDD_ACC_003.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from trading_system.accounts.tax_model import (
    FranceCTOTaxModel,
    PositionMeta,
    TaxModel,
)
from trading_system.models.money import Currency, Money
from trading_system.result import Ok, Result


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), Currency.EUR)


def _meta() -> PositionMeta:
    return PositionMeta(holding_period_days=400, instrument_class="stock")


# ---------------------------------------------------------------------------
# TC_ACC_004 — Protocol conformance + France CTO default
# ---------------------------------------------------------------------------


def test_france_cto_satisfies_tax_model_protocol() -> None:
    assert isinstance(FranceCTOTaxModel(), TaxModel)


def test_france_cto_default_rate_is_thirty_percent() -> None:
    assert FranceCTOTaxModel().rate == Decimal("0.30")


def test_france_cto_applies_thirty_percent_to_positive_gain() -> None:
    model = FranceCTOTaxModel()
    res = model.apply_realized(_eur("100"), _meta())
    # 100 × (1 - 0.30) = 70
    assert res.unwrap() == _eur("70.00")


def test_france_cto_losses_pass_through_pre_tax() -> None:
    """REQ_C_TAX_001 — losses are not taxed; they reduce the
    operator's overall tax base outside this engine's scope."""
    model = FranceCTOTaxModel()
    res = model.apply_realized(_eur("-50"), _meta())
    assert res.unwrap() == _eur("-50")


def test_france_cto_zero_gain_passes_through() -> None:
    model = FranceCTOTaxModel()
    res = model.apply_realized(_eur("0"), _meta())
    assert res.unwrap() == _eur("0")


def test_france_cto_applies_tax_to_dividends() -> None:
    model = FranceCTOTaxModel()
    res = model.apply_dividend(_eur("10"), _meta())
    assert res.unwrap() == _eur("7.00")


def test_france_cto_zero_dividend_passes_through() -> None:
    model = FranceCTOTaxModel()
    res = model.apply_dividend(_eur("0"), _meta())
    assert res.unwrap() == _eur("0")


def test_rate_must_lie_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="rate"):
        FranceCTOTaxModel(rate=Decimal("-0.1"))
    with pytest.raises(ValueError, match="rate"):
        FranceCTOTaxModel(rate=Decimal("1.5"))


# ---------------------------------------------------------------------------
# Second tax model admissible without touching ``tax/``
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PaperZeroTaxModel:
    """Paper / zero-tax stub used only in tests."""

    def apply_realized(
        self, gain: Money, position_meta: PositionMeta
    ) -> Result[Money, str]:
        return Ok(gain)

    def apply_dividend(
        self, amount: Money, position_meta: PositionMeta
    ) -> Result[Money, str]:
        return Ok(amount)


def test_second_tax_model_admissible() -> None:
    """REQ_F_ACC_005 — additional models register through the
    Protocol; no module under ``tax/`` needs to change."""
    paper = _PaperZeroTaxModel()
    assert isinstance(paper, TaxModel)
    # Gains pass through entirely under paper / zero-tax.
    assert paper.apply_realized(_eur("100"), _meta()).unwrap() == _eur("100")
    assert paper.apply_dividend(_eur("10"), _meta()).unwrap() == _eur("10")
