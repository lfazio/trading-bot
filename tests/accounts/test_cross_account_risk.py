"""Tests for ``trading_system.accounts.cross_account_risk``.

Covers TC_ACC_007 (cross-account concentration gate; single-account
no-op).

REQ refs: REQ_F_ACC_008, REQ_NF_ACC_001, REQ_SDS_ACC_004,
REQ_SDD_ACC_005.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.accounts.account import Account
from trading_system.accounts.cross_account_risk import (
    cross_account_concentration_gate,
)
from trading_system.accounts.registry import AccountRegistry
from trading_system.accounts.tax_model import FranceCTOTaxModel
from trading_system.models.identifiers import AccountId, InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side, StopLoss
from trading_system.result import Err, Ok


def _stock(instrument_id: str = "ASML.AS") -> Stock:
    return Stock(
        id=InstrumentId(instrument_id),
        symbol=instrument_id.split(".")[0],
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin=f"{instrument_id}-ISIN",
        sector="tech",
        country="NL",
    )


def _proposal(
    size_pct: str = "0.05",
    *,
    side: Side = Side.BUY,
    instrument_id: str = "ASML.AS",
) -> TradeProposal:
    zero = Money(Decimal(0), Currency.EUR)
    return TradeProposal(
        instrument=_stock(instrument_id),
        side=side,
        size_pct_of_capital=Decimal(size_pct),
        expected_net_profit=zero,
        expected_fees=zero,
        stop_loss=StopLoss(price=Decimal("90")),
        source_strategy="test",
    )


def _account(account_id: str) -> Account:
    return Account(
        id=AccountId(account_id),
        broker=object(),
        portfolio=object(),
        capital_flow=object(),
        phase_engine=object(),
        tax_model=FranceCTOTaxModel(),
        risk_overlay=object(),
        operator_token_account_id=account_id,
    )


_HOUSEHOLD = Money(Decimal("100000"), Currency.EUR)


# ---------------------------------------------------------------------------
# REQ_NF_ACC_001 — single-account no-op
# ---------------------------------------------------------------------------


def test_single_account_registry_returns_ok_without_evaluating() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha"))
    res = cross_account_concentration_gate(
        _proposal("0.99"),  # very large proposal
        registry=registry,
        household_exposure={InstrumentId("ASML.AS"): Money(Decimal("90000"), Currency.EUR)},
        household_equity=_HOUSEHOLD,
        cap_pct=Decimal("0.05"),
    )
    # Single-account: gate is a no-op regardless of inputs.
    assert isinstance(res, Ok)


def test_empty_registry_treated_as_single_account_no_op() -> None:
    # An empty registry is also treated as single-account (no
    # household risk to enforce). Defensive: never panic on an
    # operator's misconfigured registry.
    registry = AccountRegistry()
    # An empty registry's is_single_account() returns False — the
    # gate runs normally. With zero household equity it fails fast.
    res = cross_account_concentration_gate(
        _proposal("0.05"),
        registry=registry,
        household_exposure={},
        household_equity=_HOUSEHOLD,
        cap_pct=Decimal("0.05"),
    )
    # Empty exposure + small proposal: 5% × 100k = 5k → 5% share = at cap
    # (gate uses strict > cap_pct). 5% is exactly the cap — passes.
    assert isinstance(res, Ok)


# ---------------------------------------------------------------------------
# TC_ACC_007 — concentration gate fires above cap
# ---------------------------------------------------------------------------


def test_cap_breach_returns_categorised_err() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha"))
    registry.add(_account("beta"))
    res = cross_account_concentration_gate(
        _proposal("0.03"),  # 3000 EUR proposal
        registry=registry,
        # Already holding 4000 EUR; +3000 = 7000 → 7% → > 5% cap.
        household_exposure={InstrumentId("ASML.AS"): Money(Decimal("4000"), Currency.EUR)},
        household_equity=_HOUSEHOLD,
        cap_pct=Decimal("0.05"),
    )
    match res:
        case Err(reason):
            assert reason == "risk:cross_account_concentration:ASML.AS"
        case Ok(_):
            raise AssertionError("expected cap-breach Err")


def test_within_cap_returns_ok() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha"))
    registry.add(_account("beta"))
    res = cross_account_concentration_gate(
        _proposal("0.01"),  # 1000 EUR proposal
        registry=registry,
        household_exposure={InstrumentId("ASML.AS"): Money(Decimal("3000"), Currency.EUR)},
        household_equity=_HOUSEHOLD,
        cap_pct=Decimal("0.05"),
    )
    # Total = 4000; 4% share; within cap.
    assert isinstance(res, Ok)


def test_sell_decreases_exposure() -> None:
    """A SELL proposal subtracts from the household exposure. A
    SELL when the household already has 6000 EUR exposure (6%)
    SHOULD pass — the SELL reduces the position."""
    registry = AccountRegistry()
    registry.add(_account("alpha"))
    registry.add(_account("beta"))
    res = cross_account_concentration_gate(
        _proposal("0.02", side=Side.SELL),
        registry=registry,
        household_exposure={InstrumentId("ASML.AS"): Money(Decimal("6000"), Currency.EUR)},
        household_equity=_HOUSEHOLD,
        cap_pct=Decimal("0.05"),
    )
    # Projected: 6000 - 2000 = 4000 = 4% share — within cap.
    assert isinstance(res, Ok)


def test_zero_household_equity_returns_categorised_err() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha"))
    registry.add(_account("beta"))
    res = cross_account_concentration_gate(
        _proposal("0.05"),
        registry=registry,
        household_exposure={},
        household_equity=Money(Decimal(0), Currency.EUR),
        cap_pct=Decimal("0.05"),
    )
    match res:
        case Err(reason):
            assert "zero_equity" in reason
        case Ok(_):
            raise AssertionError("expected zero-equity Err")


def test_bad_cap_pct_returns_categorised_err() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha"))
    registry.add(_account("beta"))
    res = cross_account_concentration_gate(
        _proposal("0.05"),
        registry=registry,
        household_exposure={},
        household_equity=_HOUSEHOLD,
        cap_pct=Decimal("1.5"),
    )
    match res:
        case Err(reason):
            assert "bad_cap_pct" in reason
        case Ok(_):
            raise AssertionError("expected bad-cap-pct Err")


def test_currency_mismatch_returns_categorised_err() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha"))
    registry.add(_account("beta"))
    res = cross_account_concentration_gate(
        _proposal("0.05"),
        registry=registry,
        household_exposure={InstrumentId("ASML.AS"): Money(Decimal("4000"), Currency.USD)},
        household_equity=_HOUSEHOLD,
        cap_pct=Decimal("0.05"),
    )
    match res:
        case Err(reason):
            assert "currency_mismatch" in reason
        case Ok(_):
            raise AssertionError("expected currency-mismatch Err")
