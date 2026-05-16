"""Tests for ``accounts.factory.build_default_registry`` (REQ_F_ACC_003
/ REQ_NF_ACC_001).

The factory synthesises a single-account ``AccountRegistry`` from
the legacy ``system.yaml`` / ``phases.yaml`` / ``risk.yaml`` files
when ``accounts.yaml`` is absent. The account's id is
:data:`DEFAULT_ACCOUNT_ID` so the persistence layer's
``account_id`` columns line up without a migration.
"""

from __future__ import annotations

from pathlib import Path

from trading_system.accounts.factory import (
    AccountComponents,
    build_default_registry,
)
from trading_system.accounts.registry import (
    AccountRegistry,
    is_default_single_account,
)
from trading_system.accounts.tax_model import FranceCTOTaxModel
from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID
from trading_system.result import Err, Ok


class _Sentinel:
    def __init__(self, name: str) -> None:
        self.name = name


def _components() -> AccountComponents:
    return AccountComponents(
        broker=_Sentinel("broker"),
        portfolio=_Sentinel("portfolio"),
        capital_flow=_Sentinel("capital_flow"),
        phase_engine=_Sentinel("phase_engine"),
        risk_overlay=_Sentinel("risk_overlay"),
    )


def test_builds_single_account_registry_with_default_id(tmp_path: Path) -> None:
    result = build_default_registry(
        config_dir=tmp_path,
        components=_components(),
    )
    match result:
        case Ok(registry):
            assert isinstance(registry, AccountRegistry)
            assert registry.is_single_account()
            assert is_default_single_account(registry)
        case Err(reason):
            raise AssertionError(reason)


def test_default_account_carries_france_cto_tax_model(tmp_path: Path) -> None:
    """REQ_C_TAX_001 — France CTO is the default tax model when no
    operator override is supplied."""
    registry = build_default_registry(
        config_dir=tmp_path,
        components=_components(),
    ).unwrap()
    acct = next(iter(registry.list_accounts()))
    assert isinstance(acct.tax_model, FranceCTOTaxModel)
    assert acct.tax_model.rate.compare_total(
        type(acct.tax_model.rate)("0.30")
    ) == type(acct.tax_model.rate)("0")  # Decimal("0.30") sentinel


def test_explicit_tax_model_is_threaded_through(tmp_path: Path) -> None:
    custom = FranceCTOTaxModel()
    registry = build_default_registry(
        config_dir=tmp_path,
        components=_components(),
        tax_model=custom,
    ).unwrap()
    acct = next(iter(registry.list_accounts()))
    assert acct.tax_model is custom


def test_operator_token_account_id_defaults_to_account_id(tmp_path: Path) -> None:
    registry = build_default_registry(
        config_dir=tmp_path,
        components=_components(),
    ).unwrap()
    acct = next(iter(registry.list_accounts()))
    assert acct.operator_token_account_id == str(DEFAULT_ACCOUNT_ID)


def test_operator_token_account_id_override(tmp_path: Path) -> None:
    registry = build_default_registry(
        config_dir=tmp_path,
        components=_components(),
        operator_token_account_id="custom-token-id",
    ).unwrap()
    acct = next(iter(registry.list_accounts()))
    assert acct.operator_token_account_id == "custom-token-id"


def test_factory_returns_categorised_err_on_invariant_breach(
    tmp_path: Path,
) -> None:
    """Components that violate ``Account.__post_init__`` SHALL surface
    as ``Err("accounts:factory_invariant:<reason>")`` rather than
    raising — the runtime startup path uses `match`-on-Result."""
    result = build_default_registry(
        config_dir=tmp_path,
        components=_components(),
        operator_token_account_id="",  # empty → Account invariant trips
    )
    match result:
        case Err(reason):
            assert reason.startswith("accounts:factory_invariant:")
        case _:
            raise AssertionError("expected Err")
