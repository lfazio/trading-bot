"""Tests for ``accounts.yaml_loader.load_accounts_yaml``."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_system.accounts.yaml_loader import AccountSpec, load_accounts_yaml
from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "accounts.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# AccountSpec invariants
# ---------------------------------------------------------------------------


def test_spec_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="id"):
        AccountSpec(
            id=AccountId(""),
            tax_model="france_cto",
            operator_token_account_id="alpha",
        )


def test_spec_rejects_unknown_tax_model() -> None:
    with pytest.raises(ValueError, match="tax_model"):
        AccountSpec(
            id=AccountId("alpha"),
            tax_model="unknown",
            operator_token_account_id="alpha",
        )


def test_spec_rejects_empty_token_account_id() -> None:
    with pytest.raises(ValueError, match="operator_token_account_id"):
        AccountSpec(
            id=AccountId("alpha"),
            tax_model="france_cto",
            operator_token_account_id="   ",
        )


# ---------------------------------------------------------------------------
# Loader happy path
# ---------------------------------------------------------------------------


def test_loads_single_account(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
accounts:
  - id: alpha
    tax_model: france_cto
    operator_token_account_id: alpha
""",
    )
    specs = load_accounts_yaml(p).unwrap()
    assert len(specs) == 1
    assert str(specs[0].id) == "alpha"
    assert specs[0].tax_model == "france_cto"
    assert specs[0].operator_token_account_id == "alpha"


def test_loads_multiple_accounts(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
accounts:
  - id: alpha
    tax_model: france_cto
  - id: beta
    tax_model: france_cto
    operator_token_account_id: beta-token
""",
    )
    specs = load_accounts_yaml(p).unwrap()
    assert len(specs) == 2
    assert str(specs[0].id) == "alpha"
    # tax_model defaults to france_cto when omitted.
    assert specs[0].tax_model == "france_cto"
    # operator_token_account_id defaults to id when omitted.
    assert specs[0].operator_token_account_id == "alpha"
    assert specs[1].operator_token_account_id == "beta-token"


def test_empty_file_returns_empty_tuple(tmp_path: Path) -> None:
    p = _write(tmp_path, "")
    assert load_accounts_yaml(p).unwrap() == ()


def test_absent_accounts_key_returns_empty_tuple(tmp_path: Path) -> None:
    p = _write(tmp_path, "other_section: 1\n")
    assert load_accounts_yaml(p).unwrap() == ()


# ---------------------------------------------------------------------------
# Loader error categories
# ---------------------------------------------------------------------------


def test_missing_file_returns_io_err(tmp_path: Path) -> None:
    match load_accounts_yaml(tmp_path / "ghost.yaml"):
        case Err(reason):
            assert reason.startswith("config:io:")
        case _:
            raise AssertionError("expected Err")


def test_malformed_yaml_returns_parse_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "accounts: {a: b\n")
    match load_accounts_yaml(p):
        case Err(reason):
            assert reason.startswith("config:parse:")
        case _:
            raise AssertionError("expected Err")


def test_non_mapping_top_level_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "- one\n- two\n")
    match load_accounts_yaml(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_non_list_accounts_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "accounts: not-a-list\n")
    match load_accounts_yaml(p):
        case Err(reason):
            assert reason.startswith("config:schema:") and "list" in reason
        case _:
            raise AssertionError("expected Err")


def test_unknown_tax_model_returns_invariant_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "accounts:\n  - id: alpha\n    tax_model: voodoo\n")
    match load_accounts_yaml(p):
        case Err(reason):
            assert reason.startswith("config:invariant:") and "tax_model" in reason
        case _:
            raise AssertionError("expected Err")


def test_duplicate_id_returns_accounts_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "accounts:\n  - id: alpha\n  - id: alpha\n",
    )
    match load_accounts_yaml(p):
        case Err(reason):
            assert reason == "accounts:duplicate_id:alpha"
        case _:
            raise AssertionError("expected Err")


def test_non_string_id_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "accounts:\n  - id: 42\n")
    match load_accounts_yaml(p):
        case Err(reason):
            assert reason.startswith("config:schema:") and "id" in reason
        case _:
            raise AssertionError("expected Err")


def test_accounts_item_not_a_mapping_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "accounts:\n  - alpha\n  - beta\n")
    match load_accounts_yaml(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")
