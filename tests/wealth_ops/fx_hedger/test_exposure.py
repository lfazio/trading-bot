"""Tests for ``trading_system.wealth_ops.fx_hedger.exposure``.

Covers TC_FXH_003 (compute_fx_exposure pure semantics + filters base
+ deterministic).

REQ refs: REQ_F_FXH_002, REQ_NF_FXH_001, REQ_SDD_FXH_003.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.money import Currency, Money
from trading_system.wealth_ops.fx_hedger.exposure import (
    MarkedPosition,
    compute_fx_exposure,
)


def _pos(currency: Currency, amount: str) -> MarkedPosition:
    return MarkedPosition(
        currency=currency,
        value_in_base=Money(Decimal(amount), Currency.EUR),
    )


# ---------------------------------------------------------------------------
# TC_FXH_003 — pure semantics
# ---------------------------------------------------------------------------


def test_filters_base_currency_and_computes_shares() -> None:
    positions = [
        _pos(Currency.EUR, "100"),  # base — filtered out
        _pos(Currency.USD, "30"),
        _pos(Currency.GBP, "20"),
    ]
    exposures = compute_fx_exposure(
        positions,
        base_currency=Currency.EUR,
        household_equity=Money(Decimal("150"), Currency.EUR),
    )
    assert exposures == {
        Currency.USD: Decimal("30") / Decimal("150"),
        Currency.GBP: Decimal("20") / Decimal("150"),
    }
    assert Currency.EUR not in exposures


def test_pure_replay_deterministic() -> None:
    positions = [
        _pos(Currency.USD, "30"),
        _pos(Currency.GBP, "20"),
    ]
    args = dict(
        positions=positions,
        base_currency=Currency.EUR,
        household_equity=Money(Decimal("150"), Currency.EUR),
    )
    a = compute_fx_exposure(**args)  # type: ignore[arg-type]
    b = compute_fx_exposure(**args)  # type: ignore[arg-type]
    assert dict(a) == dict(b)


def test_aggregates_multiple_positions_in_same_currency() -> None:
    positions = [
        _pos(Currency.USD, "30"),
        _pos(Currency.USD, "20"),
    ]
    exposures = compute_fx_exposure(
        positions,
        base_currency=Currency.EUR,
        household_equity=Money(Decimal("100"), Currency.EUR),
    )
    assert exposures == {Currency.USD: Decimal("0.50")}


def test_zero_share_currencies_omitted() -> None:
    positions = [
        _pos(Currency.USD, "0"),  # zero value — omitted
        _pos(Currency.GBP, "20"),
    ]
    exposures = compute_fx_exposure(
        positions,
        base_currency=Currency.EUR,
        household_equity=Money(Decimal("100"), Currency.EUR),
    )
    assert Currency.USD not in exposures
    assert exposures[Currency.GBP] == Decimal("0.20")


def test_empty_positions_returns_empty_mapping() -> None:
    exposures = compute_fx_exposure(
        positions=(),
        base_currency=Currency.EUR,
        household_equity=Money(Decimal("100"), Currency.EUR),
    )
    assert dict(exposures) == {}


def test_marked_position_rejects_negative_value() -> None:
    with pytest.raises(ValueError, match="value_in_base"):
        MarkedPosition(
            currency=Currency.USD,
            value_in_base=Money(Decimal("-1"), Currency.EUR),
        )


def test_household_equity_currency_must_match_base() -> None:
    with pytest.raises(ValueError, match="household_equity.currency must equal"):
        compute_fx_exposure(
            positions=(),
            base_currency=Currency.EUR,
            household_equity=Money(Decimal("100"), Currency.USD),
        )


def test_household_equity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="household_equity must be > 0"):
        compute_fx_exposure(
            positions=(),
            base_currency=Currency.EUR,
            household_equity=Money(Decimal("0"), Currency.EUR),
        )
    with pytest.raises(ValueError, match="household_equity must be > 0"):
        compute_fx_exposure(
            positions=(),
            base_currency=Currency.EUR,
            household_equity=Money(Decimal("-1"), Currency.EUR),
        )


def test_position_value_currency_must_match_base() -> None:
    # MarkedPosition's contract: value_in_base should already be in the
    # base currency. A mismatch is a programmer error.
    bad_pos = MarkedPosition(
        currency=Currency.USD,
        value_in_base=Money(Decimal("30"), Currency.USD),  # WRONG currency
    )
    with pytest.raises(ValueError, match="value_in_base.currency must"):
        compute_fx_exposure(
            positions=[bad_pos],
            base_currency=Currency.EUR,
            household_equity=Money(Decimal("100"), Currency.EUR),
        )
