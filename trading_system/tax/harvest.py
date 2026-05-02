"""Tax-loss harvester (Phase 5+).

Identifies realized losses within the current fiscal year that can
offset capital gains accumulated to date. Output is a sorted list of
suggestions — *not* an execution plan; the portfolio / strategy layer
decides whether to act on each suggestion.

REQ refs:
- REQ_F_TAX_006 — Phase-5+ tax-loss harvesting capability.
- REQ_F_TAX_004 — output remains an after-tax optimization signal.
- REQ_SDS_MOD_003 — pure functions; ``TaxConfig`` injected.
- REQ_SDD_TYP_001 — ``Decimal``-backed money throughout.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from trading_system.models.money import Money
from trading_system.tax.config import DECEMBER, TaxConfig


@dataclass(frozen=True, slots=True)
class Realization:
    """A closed-position realized PnL record.

    ``gross`` is signed: positive => realized gain, negative =>
    realized loss. ``position_id`` is an opaque caller-supplied key
    (e.g., ``InstrumentId`` or a portfolio-level position id) — the
    harvester does not interpret it.
    """

    position_id: str
    realized_at: datetime
    gross: Money

    def __post_init__(self) -> None:
        if not self.position_id:
            raise ValueError("Realization.position_id must be non-empty")


@dataclass(frozen=True, slots=True)
class HarvestSuggestion:
    """An identified loss-side realization that may offset open gains.

    ``loss_magnitude`` is always non-negative (the absolute value of
    the losing realization's gross PnL). Consumers SHALL convert back
    to a signed reduction when applying the offset.
    """

    position_id: str
    loss_magnitude: Money

    def __post_init__(self) -> None:
        if self.loss_magnitude.amount < 0:
            raise ValueError(
                f"HarvestSuggestion.loss_magnitude must be >= 0, got {self.loss_magnitude.amount}"
            )


def fiscal_year_of(at: datetime, cfg: TaxConfig) -> int:
    """Return the fiscal year that contains ``at`` under ``cfg``.

    For a calendar-year regime (``fiscal_year_end_month = 12``,
    France-CTO default) this collapses to ``at.year``.
    """
    if cfg.fiscal_year_end_month == DECEMBER:
        return at.year
    # End-month other than December: the fiscal year is named after the
    # year in which it ENDS. Months > end_month belong to the next FY.
    if at.month > cfg.fiscal_year_end_month:
        return at.year + 1
    return at.year


def harvest_losses(
    cfg: TaxConfig,
    ledger: Sequence[Realization],
    fiscal_year: int,
    capital_gains_so_far: Money,
) -> list[HarvestSuggestion]:
    """Find loss-side realizations that can offset accumulated gains
    within ``fiscal_year`` (REQ_F_TAX_006).

    Algorithm:
      1. Filter to losses (``gross < 0``) within ``fiscal_year``.
      2. Sort by magnitude descending — largest losses first
         minimize the number of suggestions to act on.
      3. Greedy selection until cumulative loss meets or exceeds the
         outstanding gains.

    Returns ``[]`` if ``capital_gains_so_far <= 0`` (nothing to offset).
    Currency consistency is asserted; loss currency MUST match the
    gains currency.
    """
    if capital_gains_so_far.amount <= 0:
        return []

    eligible: list[Realization] = []
    for r in ledger:
        if fiscal_year_of(r.realized_at, cfg) != fiscal_year:
            continue
        if r.gross.amount >= 0:
            continue
        assert r.gross.currency == capital_gains_so_far.currency, (
            f"harvest_losses cross-currency: {r.gross.currency} vs {capital_gains_so_far.currency}"
        )
        eligible.append(r)

    # Sort by gross ascending (most negative first => largest loss first).
    eligible.sort(key=lambda r: r.gross.amount)

    suggestions: list[HarvestSuggestion] = []
    remaining = capital_gains_so_far.amount
    for r in eligible:
        if remaining <= 0:
            break
        suggestions.append(
            HarvestSuggestion(
                position_id=r.position_id,
                loss_magnitude=Money(-r.gross.amount, r.gross.currency),
            )
        )
        remaining += r.gross.amount  # gross is negative
    return suggestions
