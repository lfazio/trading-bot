"""Default-registry factory — REQ_F_ACC_003 / REQ_NF_ACC_001.

When ``config/accounts.yaml`` is absent the runtime synthesises a
single ``Account(id=DEFAULT_ACCOUNT_ID, …)`` from the legacy
``system.yaml`` + ``phases.yaml`` + ``risk.yaml`` files so a
single-account deployment keeps working without operator action.
The persistence layer's ``account_id`` columns already default to
``"default"`` (REQ_F_PER_009), so no schema migration runs on this
path.

The factory does NOT construct the broker / portfolio / capital_flow
cursors — those live on the existing runtime and the wiring threads
them in alongside the `Account`. Phase-B sub-CRs replace the
account fields with their concrete counterparts; this factory ships
the entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trading_system.accounts.account import Account
from trading_system.accounts.registry import AccountRegistry
from trading_system.accounts.tax_model import FranceCTOTaxModel
from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId
from trading_system.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class AccountComponents:
    """Cursor bundle for the components that an ``Account`` carries
    by reference. The runtime owns the cursors; the factory wires
    them into the ``Account`` aggregate.

    Phase-A scope: any caller can pass opaque sentinels (the
    foundation slice uses ``_AnyComponent = Any``); Phase-B sub-CRs
    will replace the field types with the concrete `BrokerAdapter`,
    `Portfolio`, etc. as they migrate to account-aware signatures.
    """

    broker: Any
    portfolio: Any
    capital_flow: Any
    phase_engine: Any
    risk_overlay: Any


def build_default_registry(
    *,
    config_dir: Path,  # noqa: ARG001 — kept for Phase-B accounts.yaml lookup
    components: AccountComponents,
    tax_model: Any = None,
    operator_token_account_id: str | None = None,
) -> Result[AccountRegistry, str]:
    """Synthesise the legacy single-account ``AccountRegistry``.

    Returns ``Ok(registry)`` containing one ``Account`` whose id is
    :data:`DEFAULT_ACCOUNT_ID`. ``tax_model`` defaults to a fresh
    ``FranceCTOTaxModel()`` (REQ_C_TAX_001 default).
    ``operator_token_account_id`` defaults to the account id string
    so the token-claim binding (REQ_F_ACC_010) is symmetric.
    """
    tm = tax_model if tax_model is not None else FranceCTOTaxModel()
    aid: AccountId = DEFAULT_ACCOUNT_ID
    # ``or`` substitution would mask an explicit empty string and let
    # the Account constructor reject it later with a generic message;
    # check explicitly for None so the empty-string case bubbles
    # through to Account.__post_init__ as a categorised Err.
    token_aid = (
        operator_token_account_id
        if operator_token_account_id is not None
        else str(aid)
    )
    try:
        account = Account(
            id=aid,
            broker=components.broker,
            portfolio=components.portfolio,
            capital_flow=components.capital_flow,
            phase_engine=components.phase_engine,
            tax_model=tm,
            risk_overlay=components.risk_overlay,
            operator_token_account_id=token_aid,
        )
    except (TypeError, ValueError) as e:
        return Err(f"accounts:factory_invariant:{e!s}")
    registry = AccountRegistry()
    add_result = registry.add(account)
    if isinstance(add_result, Err):
        return Err(add_result.error)
    return Ok(registry)
