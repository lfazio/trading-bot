"""``OverlayLedger`` — append-only OPEN→CLOSED cursor.

REQ refs: REQ_F_HOV_005, REQ_C_TAX_001, REQ_SDD_HOV_004.

Deterministic mark formula: ``notional × (current_index_level /
entry_index_level - 1)`` — no rounding, no fee deduction, no
clamping. Deterministic carry: ``notional × carry_pct_per_year ×
elapsed_days / 365``. Tax treatment per REQ_C_TAX_001: gains ×
``(1 - tax_rate)``, losses pass through pre-tax.

The ledger keeps `Decimal` precision end-to-end — no float
intermediate, no premature quantisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_system.institutional.hedge_overlay.errors import OverlayError
from trading_system.institutional.hedge_overlay.instruments import (
    IndexFuturePosition,
    OverlayPositionState,
)
from trading_system.institutional.hedge_overlay.policy import OverlayPolicy
from trading_system.result import Err, Ok, Result


@dataclass(slots=True)
class OverlayLedger:
    """Append-only mutable cursor — no other module SHALL hold a
    write reference (REQ_SDS_HOV_002 mirror of REQ_SDS_MOD_010).

    ``tax_rate`` defaults to ``Decimal("0.30")`` matching the
    France-CTO PFU flat rate (REQ_C_TAX_001). Operators with a
    different per-account ``TaxModel`` can pass an explicit rate at
    construction; the rate is frozen on the ledger and applies to
    every realised position uniformly.
    """

    base_currency: str = "EUR"
    tax_rate: Decimal = Decimal("0.30")
    _positions: list[IndexFuturePosition] = field(default_factory=list)
    _next_id: int = 1

    def open(
        self,
        *,
        benchmark: str,
        notional: Decimal,
        entry_index_level: Decimal,
        at: datetime,
    ) -> IndexFuturePosition:
        pos = IndexFuturePosition(
            id=self._next_id,
            benchmark=benchmark,
            notional=notional,
            entry_index_level=entry_index_level,
            entry_at=at,
        )
        self._positions.append(pos)
        self._next_id += 1
        return pos

    def close(
        self,
        *,
        position_id: int,
        exit_index_level: Decimal,
        at: datetime,
    ) -> Result[IndexFuturePosition, OverlayError]:
        for i, p in enumerate(self._positions):
            if p.id != position_id:
                continue
            if p.state is OverlayPositionState.CLOSED:
                return Err(
                    OverlayError(
                        f"hov:already_closed:{position_id}",
                        f"position {position_id} already closed at {p.closed_at}",
                    )
                )
            closed = IndexFuturePosition(
                id=p.id,
                benchmark=p.benchmark,
                notional=p.notional,
                entry_index_level=p.entry_index_level,
                entry_at=p.entry_at,
                state=OverlayPositionState.CLOSED,
                exit_index_level=exit_index_level,
                closed_at=at,
            )
            self._positions[i] = closed
            return Ok(closed)
        return Err(OverlayError(f"hov:not_found:{position_id}"))

    def mark(
        self,
        *,
        position: IndexFuturePosition,
        current_index_level: Decimal,
    ) -> Decimal:
        """REQ_F_HOV_005 / REQ_SDD_HOV_004 — deterministic mark.

        ``notional × (current_index_level / entry_index_level - 1)`` —
        no rounding, no fee deduction. The caller decides whether to
        further quantise for display.
        """
        return position.notional * (
            current_index_level / position.entry_index_level - Decimal("1")
        )

    def carry_cost(
        self,
        *,
        position: IndexFuturePosition,
        elapsed_days: int,
        policy: OverlayPolicy,
    ) -> Decimal:
        """REQ_SDD_HOV_004 — deterministic carry.

        ``notional × carry_pct_per_year × elapsed_days / 365``. The
        caller is responsible for ``elapsed_days >= 0``; the formula
        is linear and handles 0 cleanly.
        """
        return (
            position.notional
            * policy.carry_pct_per_year
            * (Decimal(elapsed_days) / Decimal("365"))
        )

    def realized_pnl_gross(self) -> Decimal:
        """Sum of realised P&L across every CLOSED position."""
        total = Decimal("0")
        for p in self._positions:
            if p.state is OverlayPositionState.CLOSED:
                assert p.exit_index_level is not None  # invariant
                total += p.notional * (
                    p.exit_index_level / p.entry_index_level - Decimal("1")
                )
        return total

    def realized_pnl_after_tax(self) -> Decimal:
        """REQ_C_TAX_001 family — gains × ``(1 - tax_rate)``; losses
        pass through pre-tax (the brokerage does not refund tax on
        capital losses under France CTO)."""
        total = Decimal("0")
        for p in self._positions:
            if p.state is not OverlayPositionState.CLOSED:
                continue
            assert p.exit_index_level is not None
            pnl = p.notional * (
                p.exit_index_level / p.entry_index_level - Decimal("1")
            )
            if pnl > 0:
                total += pnl * (Decimal("1") - self.tax_rate)
            else:
                total += pnl
        return total

    def positions(self) -> tuple[IndexFuturePosition, ...]:
        """Read-only snapshot of the ledger's positions in insertion
        order. The internal list SHALL NOT be exposed for mutation."""
        return tuple(self._positions)
