"""``FXHedgeLedger`` — append-only ledger of ``FXForward`` rows.

The single mutable element of the ``fx_hedger`` package
(REQ_SDS_FXH_002 / REQ_SDS_MOD_010). Open / close / mark are the
public surface; closing an already-closed or unknown forward surfaces
a categorised ``Err``. The mark formula is pure
(``notional × (current / entry - 1)``) so daily marks replay
bit-identically given the same fx-rate series.

REQ refs: REQ_F_FXH_005, REQ_F_FXH_006, REQ_SDD_FXH_004,
REQ_SDD_FXH_005.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_system.models.money import Currency, Money
from trading_system.result import Err, Ok, Result
from trading_system.wealth_ops.fx_hedger.forward import (
    FXForward,
    FXForwardState,
    ForwardId,
    HedgeProposal,
)


_DEFAULT_TAX_RATE = Decimal("0.30")    # France CTO PFU (REQ_C_TAX_001)


@dataclass(slots=True)
class FXHedgeLedger:
    """Append-only ledger; the only mutable element of the package."""

    _forwards: list[FXForward] = field(default_factory=list)
    _next_id: int = 1

    def open(
        self,
        proposal: HedgeProposal,
        *,
        entry_fx_rate: Decimal,
        opened_at: datetime,
    ) -> FXForward:
        """Open a new forward from a proposal. Returns the constructed
        ``FXForward`` (``state=OPEN``)."""
        forward = FXForward(
            id=ForwardId(f"fwd-{self._next_id}"),
            currency=proposal.currency,
            base_currency=proposal.base_currency,
            notional=proposal.hedged_notional(),
            entry_fx_rate=entry_fx_rate,
            opened_at=opened_at,
            state=FXForwardState.OPEN,
        )
        self._forwards.append(forward)
        self._next_id += 1
        return forward

    def close(
        self,
        forward_id: ForwardId,
        *,
        exit_fx_rate: Decimal,
        closed_at: datetime,
    ) -> Result[Money, str]:
        """Close an open forward at ``exit_fx_rate``. Returns the
        realised P&L in the base currency, or a categorised
        ``Err``."""
        for index, existing in enumerate(self._forwards):
            if existing.id != forward_id:
                continue
            if existing.state is FXForwardState.CLOSED:
                return Err(f"fxh:already_closed:{forward_id}")
            closed = FXForward(
                id=existing.id,
                currency=existing.currency,
                base_currency=existing.base_currency,
                notional=existing.notional,
                entry_fx_rate=existing.entry_fx_rate,
                opened_at=existing.opened_at,
                state=FXForwardState.CLOSED,
                exit_fx_rate=exit_fx_rate,
                closed_at=closed_at,
            )
            self._forwards[index] = closed
            return Ok(mark(closed, exit_fx_rate))
        return Err(f"fxh:not_found:{forward_id}")

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    def open_forwards(self) -> tuple[FXForward, ...]:
        return tuple(f for f in self._forwards if f.state is FXForwardState.OPEN)

    def closed_forwards(self) -> tuple[FXForward, ...]:
        return tuple(f for f in self._forwards if f.state is FXForwardState.CLOSED)

    def all_forwards(self) -> tuple[FXForward, ...]:
        return tuple(self._forwards)

    def realized_pnl_gross(self) -> Money:
        """Sum of realised P&L across every CLOSED forward, in base
        currency. Returns a zero-Money in the base currency when there
        are no closed forwards (the base currency is inferred from the
        first forward; an empty ledger returns ``Money(0, EUR)``)."""
        base = self._infer_base_currency()
        total = Money(Decimal(0), base)
        for f in self._forwards:
            if f.state is not FXForwardState.CLOSED:
                continue
            assert f.exit_fx_rate is not None
            total = total + mark(f, f.exit_fx_rate)
        return total

    def realized_pnl_after_tax(
        self, *, tax_rate: Decimal = _DEFAULT_TAX_RATE
    ) -> Money:
        """Apply the tax to a net-positive gross P&L; losses pass
        through pre-tax (REQ_F_FXH_006)."""
        gross = self.realized_pnl_gross()
        if gross.amount > 0:
            return gross * (Decimal(1) - tax_rate)
        return gross

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _infer_base_currency(self) -> Currency:
        if not self._forwards:
            return Currency.EUR
        return self._forwards[0].base_currency


def mark(forward: FXForward, current_fx_rate: Decimal) -> Money:
    """Pure mark-to-market formula (REQ_F_FXH_005 / REQ_SDD_FXH_005).

    Returns the marked P&L in the forward's ``base_currency`` —
    deterministic for identical ``(notional, entry_rate,
    current_rate)`` triples."""
    if current_fx_rate <= 0:
        raise ValueError(
            f"mark: current_fx_rate must be > 0, got {current_fx_rate}"
        )
    delta = current_fx_rate / forward.entry_fx_rate - Decimal(1)
    return forward.notional * delta
