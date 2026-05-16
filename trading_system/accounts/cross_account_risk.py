"""``cross_account_concentration_gate`` — household single-asset
concentration cap (REQ_F_ACC_008 / REQ_SDS_ACC_004 / REQ_SDD_ACC_005).

Runs **after** the per-account ``RiskEngine.pre_trade`` and **before**
order submission. Single-account deployments short-circuit with
``Ok(None)`` so the legacy path is unaffected (REQ_NF_ACC_001).

The gate is a pure function — the caller supplies the household's
current per-instrument exposure (from :class:`PortfolioGroup`) and
the household equity (also from the group). The function never reads
state directly so it stays trivially testable.

REQ refs: REQ_F_ACC_008, REQ_NF_ACC_001, REQ_SDS_ACC_004,
REQ_SDD_ACC_005.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from trading_system.accounts.registry import AccountRegistry
from trading_system.models.identifiers import InstrumentId
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Money
from trading_system.result import Err, Ok, Result


def cross_account_concentration_gate(
    proposal: TradeProposal,
    *,
    registry: AccountRegistry,
    household_exposure: Mapping[InstrumentId, Money],
    household_equity: Money,
    cap_pct: Decimal,
) -> Result[None, str]:
    """Reject ``proposal`` when adding its signed exposure to the
    household's existing exposure in the candidate instrument would
    push the share above ``cap_pct``.

    Single-account deployments (``registry.size() == 1``) SHALL be a
    no-op per REQ_NF_ACC_001 — the per-account risk engine's
    single-asset cap (REQ_F_RSK_002) already covers the case.

    ``cap_pct`` is the household-wide cap (typically the operator's
    most conservative per-account single-asset cap, or stricter).
    The Phase-6 wiring binds this from ``config/accounts.yaml``.
    """
    if registry.is_single_account():
        return Ok(None)
    if household_equity.amount <= 0:
        return Err(
            f"risk:cross_account_concentration:{proposal.instrument.id}:zero_equity"
        )
    if not (Decimal(0) < cap_pct <= Decimal(1)):
        return Err(
            "risk:cross_account_concentration:bad_cap_pct:"
            f"{cap_pct} not in (0, 1]"
        )

    instrument_id = InstrumentId(proposal.instrument.id)
    current = household_exposure.get(instrument_id)
    if current is None:
        current_amount = Decimal(0)
    else:
        if current.currency != household_equity.currency:
            return Err(
                f"risk:cross_account_concentration:{instrument_id}:"
                f"currency_mismatch:{current.currency.value}:"
                f"{household_equity.currency.value}"
            )
        current_amount = current.amount

    # Signed exposure delta from the proposal.
    proposal_amount = _signed_proposal_amount(proposal, household_equity)
    projected_amount = current_amount + proposal_amount
    projected_share = abs(projected_amount) / household_equity.amount
    if projected_share > cap_pct:
        return Err(
            f"risk:cross_account_concentration:{instrument_id}"
        )
    return Ok(None)


def _signed_proposal_amount(
    proposal: TradeProposal, household_equity: Money
) -> Decimal:
    """Convert ``proposal.size_pct_of_capital`` into a signed amount in
    the household-equity currency.

    BUY contributes a positive delta; SELL contributes a negative
    delta. The size is multiplied by household equity (the v1
    simplification — Phase-6 wiring may use per-account equity if
    the proposal originates from a single account)."""
    from trading_system.models.trading import Side

    raw = household_equity.amount * proposal.size_pct_of_capital
    if proposal.side is Side.BUY:
        return raw
    return -raw
