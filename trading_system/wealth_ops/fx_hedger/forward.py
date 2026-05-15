"""Frozen row shapes for the FX hedger.

REQ refs: REQ_F_FXH_005, REQ_SDD_FXH_001.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType

from trading_system.models.money import Currency, Money


ForwardId = NewType("ForwardId", str)


class FXForwardState(StrEnum):
    """Lifecycle state of an ``FXForward`` (REQ_SDD_TYP_003 — StrEnum)."""

    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class HedgeProposal:
    """A proposed forward hedge — the hedger's output.

    Not a position yet; the operator (or future risk-engine wiring)
    decides whether to open it through the ``FXHedgeLedger``.
    """

    currency: Currency           # the non-base currency being hedged
    base_currency: Currency
    exposure_amount: Money       # base-currency notional being hedged
    target_hedge_ratio: Decimal  # 0 < ratio <= 1 from policy
    decided_at: datetime

    def __post_init__(self) -> None:
        if self.currency == self.base_currency:
            raise ValueError(
                "HedgeProposal.currency must differ from base_currency "
                f"(both are {self.currency})"
            )
        if self.exposure_amount.currency != self.base_currency:
            raise ValueError(
                "HedgeProposal.exposure_amount.currency must equal "
                f"base_currency (got {self.exposure_amount.currency} vs "
                f"{self.base_currency})"
            )
        if not (Decimal(0) < self.target_hedge_ratio <= Decimal(1)):
            raise ValueError(
                "HedgeProposal.target_hedge_ratio must lie in (0, 1], "
                f"got {self.target_hedge_ratio}"
            )

    def hedged_notional(self) -> Money:
        """The actual notional the operator opens — exposure × hedge ratio."""
        return self.exposure_amount * self.target_hedge_ratio


@dataclass(frozen=True, slots=True)
class FXForward:
    """One forward hedge in the ledger.

    A ``CLOSED`` forward additionally carries the exit rate + close
    timestamp; the realised P&L is computed by ``ledger.mark`` against
    the exit rate (REQ_SDD_FXH_005).
    """

    id: ForwardId
    currency: Currency               # the hedged non-base currency
    base_currency: Currency
    notional: Money                  # in base currency
    entry_fx_rate: Decimal           # 1 base = entry_fx_rate target
    opened_at: datetime
    state: FXForwardState
    exit_fx_rate: Decimal | None = None
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.currency == self.base_currency:
            raise ValueError(
                "FXForward.currency must differ from base_currency "
                f"(both are {self.currency})"
            )
        if self.notional.currency != self.base_currency:
            raise ValueError(
                "FXForward.notional.currency must equal base_currency "
                f"(got {self.notional.currency} vs {self.base_currency})"
            )
        if self.entry_fx_rate <= 0:
            raise ValueError(
                f"FXForward.entry_fx_rate must be > 0, got {self.entry_fx_rate}"
            )
        if self.state is FXForwardState.CLOSED:
            if self.exit_fx_rate is None or self.closed_at is None:
                raise ValueError(
                    "CLOSED FXForward requires both exit_fx_rate and closed_at"
                )
            if self.exit_fx_rate <= 0:
                raise ValueError(
                    f"FXForward.exit_fx_rate must be > 0, got {self.exit_fx_rate}"
                )
        else:
            # OPEN must not carry exit-side fields.
            if self.exit_fx_rate is not None or self.closed_at is not None:
                raise ValueError(
                    "OPEN FXForward must not carry exit_fx_rate / closed_at"
                )
