"""Tests for the per-account tax-dispatch shim — CR-006 Phase B.

REQ refs: REQ_F_ACC_005, REQ_SDS_ACC_003, REQ_SDD_ACC_003,
REQ_C_TAX_001 (France CTO default).
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.accounts.tax_model import (
    FranceCTOTaxModel,
    PositionMeta,
)
from trading_system.models.money import Currency, Money
from trading_system.result import Ok
from trading_system.tax.engine_account import net_dividend, net_realized


_META = PositionMeta(holding_period_days=120, instrument_class="STOCK")


def test_net_realized_routes_through_france_cto_default() -> None:
    """REQ_C_TAX_001 — 30 % flat tax on gains."""
    model = FranceCTOTaxModel()
    gross = Money(Decimal("100"), Currency.EUR)
    match net_realized(model, gross, position_meta=_META):
        case Ok(net):
            assert net.amount == Decimal("70.00")
        case _:
            raise AssertionError("expected Ok")


def test_net_realized_passes_loss_through() -> None:
    """REQ_C_TAX_001 — losses pass through pre-tax."""
    model = FranceCTOTaxModel()
    loss = Money(Decimal("-50"), Currency.EUR)
    match net_realized(model, loss, position_meta=_META):
        case Ok(net):
            assert net.amount == Decimal("-50")
        case _:
            raise AssertionError("expected Ok")


def test_net_dividend_routes_through_france_cto_default() -> None:
    model = FranceCTOTaxModel()
    gross = Money(Decimal("10"), Currency.EUR)
    match net_dividend(model, gross, position_meta=_META):
        case Ok(net):
            assert net.amount == Decimal("7.0")
        case _:
            raise AssertionError("expected Ok")


def test_custom_tax_model_through_shim() -> None:
    """Any TaxModel Protocol implementation routes through the shim
    — the surface stays open for PEA / foreign tax-holiday models
    (REQ_F_ACC_005)."""

    class _ZeroTaxModel:
        def apply_realized(self, gain, position_meta):  # type: ignore[no-untyped-def]
            return Ok(gain)

        def apply_dividend(self, amount, position_meta):  # type: ignore[no-untyped-def]
            return Ok(amount)

    gross = Money(Decimal("100"), Currency.EUR)
    match net_realized(_ZeroTaxModel(), gross, position_meta=_META):
        case Ok(net):
            assert net.amount == Decimal("100")
        case _:
            raise AssertionError("expected Ok")
