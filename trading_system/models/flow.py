"""Capital-flow types — external injections and equity-curve points.

REQ refs:
- REQ_F_CFL_001 — track every external injection (amount + timestamp).
- REQ_F_CFL_002 — performance metrics exclude injections; the
  ``EquityPoint`` carries both gross and after-tax equity so the
  exclusion is computed downstream by ``capital_flow/``.
- REQ_SDD_DAT_003 — ``EquityPoint`` shape.
- REQ_SDD_TYP_001 — ``Decimal`` for percentages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.models.money import Money


@dataclass(frozen=True, slots=True)
class Injection:
    """External capital deposit. Timestamps are ordered ascending in
    the canonical injection timeline (REQ_SDD_ALG_017)."""

    amount: Money
    at: datetime
    source: str = ""

    def __post_init__(self) -> None:
        if self.amount.amount <= 0:
            raise ValueError(f"Injection.amount must be > 0, got {self.amount.amount}")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """A point on the portfolio equity curve.

    ``equity_after_tax`` is the canonical reference (REQ_F_PRT_001,
    REQ_SDD_DAT_003); ``equity_gross`` is a derived snapshot stored
    alongside for analytics. ``drawdown_pct`` is computed from the
    after-tax curve per REQ_SDD_ALG_005.
    """

    at: datetime
    equity_gross: Money
    equity_after_tax: Money
    drawdown_pct: Decimal

    def __post_init__(self) -> None:
        if self.equity_gross.currency != self.equity_after_tax.currency:
            raise ValueError("EquityPoint.equity_gross and equity_after_tax must share a currency")
        if not (Decimal(0) <= self.drawdown_pct <= Decimal(1)):
            raise ValueError(
                f"EquityPoint.drawdown_pct must lie in [0, 1], got {self.drawdown_pct}"
            )
