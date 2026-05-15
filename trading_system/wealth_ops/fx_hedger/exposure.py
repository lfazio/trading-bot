"""``compute_fx_exposure`` — pure aggregation of per-currency
exposure share against a base currency.

The caller is responsible for marking positions to base_currency
*before* invocation — this function does NOT do FX conversion; it
aggregates already-marked values. Keeps the function unit-testable
without FX-rate plumbing (REQ_SDD_FXH_003).

REQ refs: REQ_F_FXH_002, REQ_NF_FXH_001, REQ_SDD_FXH_003.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from trading_system.models.money import Currency, Money


@dataclass(frozen=True, slots=True)
class MarkedPosition:
    """A position whose value has been pre-marked into the base
    currency by the caller.

    - ``currency`` — the original listing currency (e.g., USD for a
      US-listed stock).
    - ``value_in_base`` — the position's value expressed in
      ``base_currency`` (e.g., the USD value times the EUR/USD rate).
    """

    currency: Currency
    value_in_base: Money

    def __post_init__(self) -> None:
        if self.value_in_base.amount < 0:
            raise ValueError(
                "MarkedPosition.value_in_base must be >= 0, "
                f"got {self.value_in_base.amount}"
            )


def compute_fx_exposure(
    positions: Sequence[MarkedPosition],
    *,
    base_currency: Currency,
    household_equity: Money,
) -> Mapping[Currency, Decimal]:
    """Returns per-currency exposure share against ``base_currency``.

    Pure function. Zero-share currencies and the base currency itself
    are omitted so consumers iterate over a tight non-empty mapping
    (REQ_SDD_FXH_003).

    Raises ``ValueError`` if ``household_equity`` is non-positive — a
    zero or negative equity has no meaningful exposure share, and the
    division would be undefined.
    """
    if household_equity.currency != base_currency:
        raise ValueError(
            "compute_fx_exposure: household_equity.currency must equal "
            f"base_currency (got {household_equity.currency} vs {base_currency})"
        )
    if household_equity.amount <= 0:
        raise ValueError(
            "compute_fx_exposure: household_equity must be > 0, "
            f"got {household_equity.amount}"
        )

    out: dict[Currency, Decimal] = {}
    for pos in positions:
        if pos.currency == base_currency:
            continue
        if pos.value_in_base.currency != base_currency:
            raise ValueError(
                f"compute_fx_exposure: position value_in_base.currency must "
                f"equal base_currency (got {pos.value_in_base.currency} vs "
                f"{base_currency})"
            )
        share = pos.value_in_base.amount / household_equity.amount
        out[pos.currency] = out.get(pos.currency, Decimal(0)) + share

    return {c: s for c, s in out.items() if s > 0}
