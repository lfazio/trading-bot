"""CR-030 — SRDPosition dataclass + last-business-day helper.

REQ refs:
- REQ_F_SRD_003 — SRDPosition shape + invariants.
- REQ_F_SRD_005 — settlement_cycle = last business day of entry month.
- REQ_SDD_SRD_003 — constructor invariants + holiday-aware
  last-business-day computation.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from trading_system.models.instrument import Instrument


_SRDDirection = Literal["LONG", "SHORT"]


@dataclass(frozen=True, slots=True)
class SRDPosition:
    """One open SRD position (CR-030 / REQ_F_SRD_003).

    Cash exchange happens on ``settlement_cycle`` (last business
    day of the entry month, UTC). LONG profits from
    ``settlement_price > entry_price``; SHORT profits from
    ``settlement_price < entry_price``. The carry fee is charged
    monthly at the configured rate against
    ``quantity × entry_price``.
    """

    instrument: Instrument
    direction: _SRDDirection
    quantity: Decimal
    entry_price: Decimal
    entry_at: datetime
    settlement_cycle: datetime
    carry_fee_rate_monthly: Decimal = Decimal("0.0025")
    auto_rollover: bool = False

    def __post_init__(self) -> None:
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(
                f"SRDPosition.direction must be 'LONG' or 'SHORT', "
                f"got {self.direction!r}"
            )
        if self.quantity <= 0:
            raise ValueError(
                f"SRDPosition.quantity must be > 0, got {self.quantity}"
            )
        if self.entry_price <= 0:
            raise ValueError(
                f"SRDPosition.entry_price must be > 0, got {self.entry_price}"
            )
        if self.carry_fee_rate_monthly < 0:
            raise ValueError(
                f"SRDPosition.carry_fee_rate_monthly must be >= 0, "
                f"got {self.carry_fee_rate_monthly}"
            )


def last_business_day_of_month(
    at: datetime, *, holidays: frozenset[date] = frozenset()
) -> datetime:
    """CR-030 (REQ_F_SRD_005 / REQ_SDD_SRD_003) — return the last
    business day of ``at.month`` as a UTC midnight datetime.

    Walks backward from the calendar end of the month past
    weekends + the operator-supplied ``holidays`` set so an
    Euronext early-close day (e.g. Christmas Eve) rolls to the
    preceding business day. Pure-Python stdlib only; no pandas
    dependency so the determinism contract (REQ_NF_SRD_001)
    holds without external state.
    """
    last_day = calendar.monthrange(at.year, at.month)[1]
    candidate = date(at.year, at.month, last_day)
    # Walk backward past weekends + holidays.
    while candidate.weekday() >= 5 or candidate in holidays:
        candidate = candidate - timedelta(days=1)
    return datetime(
        candidate.year,
        candidate.month,
        candidate.day,
        tzinfo=UTC,
    )
