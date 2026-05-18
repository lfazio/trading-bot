"""Per-account tax-dispatch shim — CR-006 Phase B (REQ_F_ACC_005 /
REQ_SDS_ACC_003 / REQ_SDD_ACC_003).

This module is the thin bridge between the legacy
``trading_system.tax.engine`` (which works against a single
``TaxConfig``) and the per-account ``TaxModel`` Protocol introduced
in CR-006 Phase A. Operators of multi-account deployments call
through these helpers; the legacy engine stays in place for the
single-account default per REQ_NF_ACC_001.

The shim is intentionally additive — no existing call site moves to
these helpers automatically. Phase 6 follow-up walks the engine's
tax invocations and migrates them per-account once the runtime fan-
out routes every trade through ``AccountRegistry.tick`` (the wiring
that this commit lands for the demo path).
"""

from __future__ import annotations

from trading_system.accounts.account import Account
from trading_system.accounts.tax_model import PositionMeta, TaxModel
from trading_system.models.money import Money
from trading_system.result import Result


def net_realized(
    tax_model: TaxModel,
    gross: Money,
    *,
    position_meta: PositionMeta,
) -> Result[Money, str]:
    """Apply the account's :class:`TaxModel` to a realised gain.

    Routes through ``TaxModel.apply_realized``; surfaces the same
    Result the Protocol method returns so callers can pattern-match
    on the categorised Err.
    """
    return tax_model.apply_realized(gross, position_meta)


def net_dividend(
    tax_model: TaxModel,
    gross: Money,
    *,
    position_meta: PositionMeta,
) -> Result[Money, str]:
    """Apply the account's :class:`TaxModel` to a gross dividend."""
    return tax_model.apply_dividend(gross, position_meta)


def for_account(account: Account) -> TaxModel:
    """Return the per-account ``TaxModel`` bound at account creation.

    Operator-facing convenience accessor — the same as
    ``account.tax_model`` but expressed as a verb the call sites
    can grep for ("which tax model does this account use?").
    """
    return account.tax_model
