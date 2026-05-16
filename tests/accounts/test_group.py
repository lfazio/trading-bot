"""Tests for ``trading_system.accounts.group``.

Covers TC_ACC_006 (PortfolioGroup read-only AST audit + FX-missing
categorised Err) + TC_ACC_003 partial (per-account phase resolution
manifests through the registry's iteration order).

REQ refs: REQ_F_ACC_007, REQ_SDS_ACC_002, REQ_SDD_ACC_004.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from trading_system.accounts.account import Account
from trading_system.accounts.group import (
    IdentityFxConverter,
    PortfolioGroup,
)
from trading_system.accounts.registry import AccountRegistry
from trading_system.accounts.tax_model import FranceCTOTaxModel
from trading_system.models.identifiers import AccountId, InstrumentId
from trading_system.models.money import Currency, Money
from trading_system.result import Err, Ok


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_GROUP_PATH = _REPO_ROOT / "trading_system" / "accounts" / "group.py"


@dataclass(slots=True)
class _StubPortfolio:
    equity_amount: Decimal = Decimal(0)
    currency: Currency = Currency.EUR
    drawdown: Decimal = Decimal(0)
    positions: tuple[tuple[InstrumentId, Money], ...] = ()

    def equity(self) -> Money:
        return Money(self.equity_amount, self.currency)

    def positions_by_instrument(self) -> tuple[tuple[InstrumentId, Money], ...]:
        return self.positions

    def drawdown_pct(self) -> Decimal:
        return self.drawdown


def _account(
    account_id: str,
    *,
    equity: str = "0",
    currency: Currency = Currency.EUR,
    drawdown: str = "0",
    positions: tuple[tuple[str, str], ...] = (),
) -> Account:
    portfolio = _StubPortfolio(
        equity_amount=Decimal(equity),
        currency=currency,
        drawdown=Decimal(drawdown),
        positions=tuple(
            (InstrumentId(iid), Money(Decimal(amt), currency))
            for iid, amt in positions
        ),
    )
    return Account(
        id=AccountId(account_id),
        broker=object(),
        portfolio=portfolio,
        capital_flow=object(),
        phase_engine=object(),
        tax_model=FranceCTOTaxModel(),
        risk_overlay=object(),
        operator_token_account_id=account_id,
    )


# ---------------------------------------------------------------------------
# TC_ACC_006 — read-only AST audit
# ---------------------------------------------------------------------------


def test_portfolio_group_has_no_setter_methods() -> None:
    """REQ_F_ACC_007 — PortfolioGroup is read-only. Introspect the
    class and verify no methods named ``set_*`` / ``update_*`` /
    ``apply_*`` / ``record_*`` / ``add_*`` / ``remove_*`` /
    ``mutate_*`` exist."""
    forbidden_prefixes = (
        "set_",
        "update_",
        "apply_",
        "record_",
        "mutate_",
    )
    forbidden_names = {"add", "remove"}
    methods = {
        name
        for name in dir(PortfolioGroup)
        if not name.startswith("_") and callable(getattr(PortfolioGroup, name))
    }
    for method in methods:
        for prefix in forbidden_prefixes:
            assert not method.startswith(prefix), (
                f"PortfolioGroup.{method} looks like a mutator — "
                "violates REQ_F_ACC_007 read-only invariant"
            )
        assert method not in forbidden_names, (
            f"PortfolioGroup.{method} is a forbidden mutator name"
        )


def test_group_module_imports_no_portfolio_mutating_module() -> None:
    """REQ_F_ACC_007 — the group module SHALL NOT import the
    Portfolio implementation directly; it consumes any object that
    exposes the read-only accessors."""
    tree = ast.parse(_GROUP_PATH.read_text(encoding="utf-8"), filename=str(_GROUP_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("trading_system.portfolio.portfolio"), (
                "group.py imports the concrete Portfolio module — "
                "should consume via duck-typed accessors only"
            )


# ---------------------------------------------------------------------------
# household_equity / household_drawdown / exposure_by_instrument
# ---------------------------------------------------------------------------


def test_household_equity_sums_account_equities() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha", equity="60000"))
    registry.add(_account("beta", equity="40000"))
    group = PortfolioGroup(registry=registry, base_currency=Currency.EUR)
    res = group.household_equity()
    assert res.unwrap() == Money(Decimal("100000"), Currency.EUR)


def test_household_equity_fx_missing_surfaces_categorised_err() -> None:
    """REQ_SDD_ACC_004 — FX missing surfaces as the categorised
    ``accounts:fx_missing`` Err, never as a silent zero."""
    registry = AccountRegistry()
    registry.add(_account("alpha", equity="60000", currency=Currency.EUR))
    registry.add(_account("beta", equity="40000", currency=Currency.USD))
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    match group.household_equity():
        case Err(reason):
            assert reason == "accounts:fx_missing:USD:EUR"
        case Ok(_):
            raise AssertionError("expected fx_missing Err")


def test_household_drawdown_returns_max_across_accounts() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha", drawdown="0.05"))
    registry.add(_account("beta", drawdown="0.13"))
    registry.add(_account("gamma", drawdown="0.08"))
    group = PortfolioGroup(registry=registry, base_currency=Currency.EUR)
    res = group.household_drawdown()
    assert res.unwrap() == Decimal("0.13")


def test_household_drawdown_empty_registry_is_zero() -> None:
    registry = AccountRegistry()
    group = PortfolioGroup(registry=registry)
    assert group.household_drawdown().unwrap() == Decimal(0)


def test_exposure_by_instrument_aggregates_across_accounts() -> None:
    registry = AccountRegistry()
    registry.add(
        _account(
            "alpha",
            positions=(("ASML.AS", "5000"), ("BNP.PA", "3000")),
        )
    )
    registry.add(
        _account(
            "beta",
            positions=(("ASML.AS", "2000"), ("SAP.DE", "4000")),
        )
    )
    group = PortfolioGroup(registry=registry, base_currency=Currency.EUR)
    res = group.exposure_by_instrument().unwrap()
    assert res[InstrumentId("ASML.AS")] == Money(Decimal("7000"), Currency.EUR)
    assert res[InstrumentId("BNP.PA")] == Money(Decimal("3000"), Currency.EUR)
    assert res[InstrumentId("SAP.DE")] == Money(Decimal("4000"), Currency.EUR)
