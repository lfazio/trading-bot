"""Pure converters between domain dataclasses and row dicts.

REQ refs:
- REQ_F_PER_005 — Decimal stored as TEXT; datetime as ISO-8601 with
  explicit timezone; no ``float`` past the persistence boundary.
- REQ_SDD_PER_003 — pure functions; no I/O; closed Err category set
  for parse failures.
- REQ_NF_PER_001 — write-then-read round-trips equal under
  structural comparison.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from trading_system.models.flow import EquityPoint
from trading_system.models.money import Currency, Money


def equity_point_to_row(p: EquityPoint, account_id: str) -> dict[str, str]:
    """Domain ``EquityPoint`` → row dict for ``equity_points``."""
    return {
        "account_id": account_id,
        "at": p.at.isoformat(),
        "equity_gross_amount": str(p.equity_gross.amount),
        "equity_gross_currency": p.equity_gross.currency.value,
        "equity_after_tax_amount": str(p.equity_after_tax.amount),
        "equity_after_tax_currency": p.equity_after_tax.currency.value,
        "drawdown_pct": str(p.drawdown_pct),
    }


def row_to_equity_point(row: dict[str, str]) -> EquityPoint:
    """Row dict → domain ``EquityPoint``. Decimal via
    ``Decimal(str(...))`` so float repr noise never leaks in."""
    return EquityPoint(
        at=datetime.fromisoformat(row["at"]),
        equity_gross=Money(
            Decimal(row["equity_gross_amount"]),
            Currency(row["equity_gross_currency"]),
        ),
        equity_after_tax=Money(
            Decimal(row["equity_after_tax_amount"]),
            Currency(row["equity_after_tax_currency"]),
        ),
        drawdown_pct=Decimal(row["drawdown_pct"]),
    )
