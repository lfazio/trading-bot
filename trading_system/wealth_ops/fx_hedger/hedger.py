"""``FXHedger.propose_hedges`` — pure threshold-gated proposal emitter.

For each non-base currency whose exposure fraction strictly exceeds
``policy.threshold_pct``, emit one ``HedgeProposal`` with notional
``exposure × policy.target_hedge_ratio``. Below-threshold currencies
are omitted, NOT down-sized — matches "above the threshold" wording
in REQ_F_FXH_003.

Iteration is sorted by ``Currency.value`` (alphabetical) so the
returned tuple is deterministic across runs (REQ_NF_FXH_001 /
REQ_SDD_FXH_002).

REQ refs: REQ_F_FXH_003, REQ_NF_FXH_001, REQ_SDD_FXH_002.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.models.money import Currency, Money
from trading_system.wealth_ops.fx_hedger.forward import HedgeProposal
from trading_system.wealth_ops.fx_hedger.policy import HedgePolicy


@dataclass(slots=True)
class FXHedger:
    """Thin wrapper carrying the frozen :class:`HedgePolicy`. The
    actual proposal computation is a pure function over the policy +
    the exposure mapping."""

    policy: HedgePolicy

    def propose_hedges(
        self,
        exposures: Mapping[Currency, Decimal],
        *,
        household_equity: Money,
        base_currency: Currency,
        now: datetime,
    ) -> tuple[HedgeProposal, ...]:
        if household_equity.currency != base_currency:
            raise ValueError(
                "FXHedger.propose_hedges: household_equity.currency must "
                f"equal base_currency (got {household_equity.currency} vs "
                f"{base_currency})"
            )

        proposals: list[HedgeProposal] = []
        # Sort by Currency.value (alphabetical) so the proposal tuple
        # is deterministic across runs (REQ_NF_FXH_001).
        for currency in sorted(exposures, key=lambda c: c.value):
            share = exposures[currency]
            # Strict greater-than (REQ_F_FXH_003 + REQ_SDD_FXH_002).
            if share <= self.policy.threshold_pct:
                continue
            exposure_amount = household_equity * share
            proposals.append(
                HedgeProposal(
                    currency=currency,
                    base_currency=base_currency,
                    exposure_amount=exposure_amount,
                    target_hedge_ratio=self.policy.target_hedge_ratio,
                    decided_at=now,
                )
            )
        return tuple(proposals)
