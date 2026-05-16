"""Tests for ``trading_system.accounts.account`` +
``trading_system.accounts.registry``.

Covers TC_ACC_001 (single-account default Account('default')) +
TC_ACC_002 (lex-by-id fan-out + replay determinism).

REQ refs: REQ_F_ACC_001, REQ_F_ACC_002, REQ_F_ACC_003,
REQ_NF_ACC_001, REQ_SDS_ACC_002, REQ_SDD_ACC_001, REQ_SDD_ACC_002.
"""

from __future__ import annotations

from typing import Any

import pytest

from trading_system.accounts.account import Account
from trading_system.accounts.registry import (
    AccountRegistry,
    is_default_single_account,
)
from trading_system.accounts.tax_model import FranceCTOTaxModel
from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId
from trading_system.result import Err, Nothing, Ok, Some


def _account(
    account_id: AccountId | str,
    *,
    token_account_id: str | None = None,
) -> Account:
    # AccountId is a NewType over str; isinstance() can't see it,
    # so we cast unconditionally — the NewType is a static-typing
    # tool, not a runtime distinction.
    aid = AccountId(str(account_id))
    # When the caller passes ``token_account_id=None`` we default to
    # str(aid); explicit empty / whitespace strings pass through so
    # the test can assert the dataclass's invariant.
    token = str(aid) if token_account_id is None else token_account_id
    return Account(
        id=aid,
        broker=_Sentinel("broker"),
        portfolio=_Sentinel("portfolio"),
        capital_flow=_Sentinel("capital_flow"),
        phase_engine=_Sentinel("phase_engine"),
        tax_model=FranceCTOTaxModel(),
        risk_overlay=_Sentinel("risk_overlay"),
        operator_token_account_id=token,
    )


class _Sentinel:
    """Marker for the Phase-6 foundation slice — the concrete types
    arrive in the runtime-wiring follow-up. Tests pass an instance
    to demonstrate the registry holds references opaquely."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover
        return f"_Sentinel({self.name!r})"


# ---------------------------------------------------------------------------
# Account invariants — REQ_SDD_ACC_001
# ---------------------------------------------------------------------------


def test_account_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="id must be non-empty"):
        _account(AccountId(""))


def test_account_rejects_empty_token_account_id() -> None:
    with pytest.raises(ValueError, match="operator_token_account_id"):
        _account(AccountId("alpha"), token_account_id="")
    with pytest.raises(ValueError, match="operator_token_account_id"):
        _account(AccountId("alpha"), token_account_id="   ")


def test_account_rejects_non_taxmodel_tax_model() -> None:
    with pytest.raises(TypeError, match="TaxModel Protocol"):
        Account(
            id=AccountId("alpha"),
            broker=_Sentinel("broker"),
            portfolio=_Sentinel("portfolio"),
            capital_flow=_Sentinel("capital_flow"),
            phase_engine=_Sentinel("phase_engine"),
            tax_model="not-a-tax-model",  # type: ignore[arg-type]
            risk_overlay=_Sentinel("risk_overlay"),
            operator_token_account_id="alpha",
        )


def test_account_is_frozen() -> None:
    acct = _account("alpha")
    with pytest.raises(Exception):
        acct.id = AccountId("beta")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TC_ACC_001 — single-account default + backwards compat
# ---------------------------------------------------------------------------


def test_default_single_account_default_id() -> None:
    """REQ_NF_ACC_001 — a legacy single-account deployment SHALL use
    the sentinel id ``"default"`` so persisted rows match the
    ``account_id`` column default."""
    acct = _account(DEFAULT_ACCOUNT_ID)
    assert str(acct.id) == "default"
    assert acct.operator_token_account_id == "default"


def test_is_default_single_account_helper() -> None:
    """The helper recognises the canonical single-account-default
    deployment shape."""
    registry = AccountRegistry()
    assert not is_default_single_account(registry)  # empty
    registry.add(_account(DEFAULT_ACCOUNT_ID))
    assert is_default_single_account(registry)
    # Second account added — no longer single-account.
    registry.add(_account("alt"))
    assert not is_default_single_account(registry)


# ---------------------------------------------------------------------------
# TC_ACC_002 — lex-by-id fan-out + replay determinism
# ---------------------------------------------------------------------------


def test_list_accounts_returns_alphabetical_order() -> None:
    """REQ_F_ACC_002 / REQ_SDS_ACC_002 / REQ_SDD_ACC_002 — iteration
    is sorted by AccountId so a multi-account backtest replays
    bit-identically under REQ_NF_DET_001."""
    registry = AccountRegistry()
    # Insert in non-alphabetical order.
    registry.add(_account("zulu"))
    registry.add(_account("alpha"))
    registry.add(_account("mike"))
    ids = [str(a.id) for a in registry.list_accounts()]
    assert ids == ["alpha", "mike", "zulu"]


def test_list_accounts_is_replay_deterministic() -> None:
    """Calling list_accounts twice returns the same tuple — never
    relies on dict-insertion order."""
    registry = AccountRegistry()
    registry.add(_account("zulu"))
    registry.add(_account("alpha"))
    a = registry.list_accounts()
    b = registry.list_accounts()
    # Identity-preserving alphabetical order across calls.
    assert [str(x.id) for x in a] == [str(x.id) for x in b]


def test_ids_iterator_is_alphabetical() -> None:
    registry = AccountRegistry()
    registry.add(_account("zulu"))
    registry.add(_account("alpha"))
    registry.add(_account("mike"))
    assert list(registry.ids()) == [AccountId("alpha"), AccountId("mike"), AccountId("zulu")]


# ---------------------------------------------------------------------------
# Registry add() / get() semantics
# ---------------------------------------------------------------------------


def test_add_duplicate_id_returns_categorised_err() -> None:
    registry = AccountRegistry()
    assert isinstance(registry.add(_account("alpha")), Ok)
    res = registry.add(_account("alpha"))
    match res:
        case Err(reason):
            assert reason == "accounts:duplicate_id:alpha"
        case Ok(_):
            raise AssertionError("expected duplicate-id Err")


def test_get_returns_some_when_present_nothing_when_absent() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha"))
    match registry.get(AccountId("alpha")):
        case Some(a):
            assert str(a.id) == "alpha"
        case _:
            raise AssertionError
    res = registry.get(AccountId("ghost"))
    assert isinstance(res, Nothing)


def test_size_and_emptiness() -> None:
    registry = AccountRegistry()
    assert registry.is_empty()
    assert registry.size() == 0
    assert not registry.is_single_account()
    registry.add(_account("alpha"))
    assert not registry.is_empty()
    assert registry.size() == 1
    assert registry.is_single_account()
    registry.add(_account("beta"))
    assert not registry.is_single_account()
    assert registry.size() == 2
