"""Loader for ``config/accounts.yaml`` — REQ_F_ACC_003 / REQ_SDD_ACC_008.

Absent file ⇒ ``Ok(())`` (signals "use the default single-account
factory"). Present file ⇒ parses a list of account *specs* —
deliberately a thin shape in Phase A. Each spec carries the
identifiers + tax-model selector + operator-token claim; the
concrete broker / portfolio / capital_flow cursors are still wired
by the Phase-B sub-CR that migrates each runtime call site.

YAML shape::

    # config/accounts.yaml (optional — absent ⇒ single-account default)
    accounts:
      - id: alpha
        tax_model: france_cto       # or: pea_5y, paper, …
        operator_token_account_id: alpha
        # Optional Phase-B fields (broker_adapter, capital, phase_overlay, …)
        # are accepted but unused in Phase A; documented to discourage
        # YAML-shape drift.
      - id: beta
        tax_model: france_cto
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok, Result, catch


# Closed set of tax-model selectors recognised by the loader.
# Adding a new model requires a new line here + the matching
# implementation in ``trading_system.accounts.tax_model``.
_TAX_MODEL_SELECTORS: frozenset[str] = frozenset({"france_cto"})


@dataclass(frozen=True, slots=True)
class AccountSpec:
    """Minimal account spec parsed from ``config/accounts.yaml``."""

    id: AccountId
    tax_model: str
    operator_token_account_id: str

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValueError("AccountSpec.id must be non-empty")
        if self.tax_model not in _TAX_MODEL_SELECTORS:
            raise ValueError(
                f"AccountSpec.tax_model must be one of "
                f"{sorted(_TAX_MODEL_SELECTORS)}, got {self.tax_model!r}"
            )
        if not self.operator_token_account_id.strip():
            raise ValueError(
                "AccountSpec.operator_token_account_id must be non-empty"
            )


def load_accounts_yaml(path: Path | str) -> Result[tuple[AccountSpec, ...], str]:
    """Parse ``config/accounts.yaml`` into a tuple of ``AccountSpec``.

    Absent file is NOT an error here — the caller checks ``path.exists()``
    first and falls back to ``factory.build_default_registry`` when it
    doesn't. This loader only fires when the operator has supplied a
    YAML. Returns ``Err("accounts:duplicate_id:<id>")`` if the YAML
    lists an id twice — the same categorised Err the registry's
    ``add`` would emit, so call sites pattern-match the same string.
    """
    p = Path(path)
    raw_result = catch(lambda: p.read_text(encoding="utf-8"), OSError)
    match raw_result:
        case Err(exc):
            return Err(f"config:io: cannot read {p}: {exc!r}")
        case Ok(text):
            raw_text = text

    parsed_result: Result[Any, BaseException] = catch(
        lambda: yaml.safe_load(raw_text), yaml.YAMLError
    )
    match parsed_result:
        case Err(exc):
            return Err(f"config:parse: invalid YAML at {p}: {exc!r}")
        case Ok(parsed):
            payload = parsed

    if payload is None:
        # Empty file — same semantics as absent. Operator intent is
        # "no specs"; the caller decides whether that's an error.
        return Ok(())
    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {p} must be a mapping, "
            f"got {type(payload).__name__}"
        )
    accounts_raw = payload.get("accounts")
    if accounts_raw is None:
        return Ok(())
    if not isinstance(accounts_raw, list):
        return Err(
            f"config:schema: 'accounts' must be a list, "
            f"got {type(accounts_raw).__name__} ({p})"
        )

    seen: set[AccountId] = set()
    specs: list[AccountSpec] = []
    for i, item in enumerate(accounts_raw):
        if not isinstance(item, Mapping):
            return Err(
                f"config:schema: accounts[{i}] must be a mapping, "
                f"got {type(item).__name__} ({p})"
            )
        id_raw = item.get("id")
        if not isinstance(id_raw, str):
            return Err(
                f"config:schema: accounts[{i}].id must be a string "
                f"(got {type(id_raw).__name__}) ({p})"
            )
        tax_raw = item.get("tax_model", "france_cto")
        if not isinstance(tax_raw, str):
            return Err(
                f"config:schema: accounts[{i}].tax_model must be a string "
                f"(got {type(tax_raw).__name__}) ({p})"
            )
        token_raw = item.get("operator_token_account_id", id_raw)
        if not isinstance(token_raw, str):
            return Err(
                f"config:schema: accounts[{i}].operator_token_account_id "
                f"must be a string (got {type(token_raw).__name__}) ({p})"
            )

        aid = AccountId(id_raw)
        if aid in seen:
            return Err(f"accounts:duplicate_id:{aid}")
        seen.add(aid)
        try:
            spec = AccountSpec(
                id=aid,
                tax_model=tax_raw,
                operator_token_account_id=token_raw,
            )
        except ValueError as e:
            return Err(f"config:invariant: {e!s} ({p})")
        specs.append(spec)

    return Ok(tuple(specs))
