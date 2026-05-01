"""Data-layer types: ``Timeframe``, ``Bar``, ``Fundamentals``.

REQ refs: REQ_SDD_TYP_001 (Decimal everywhere), REQ_SDD_TYP_003
(``Timeframe`` as ``StrEnum``), REQ_SDS_INT_002 (interface shape).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from trading_system.models.money import Money


class Timeframe(StrEnum):
    """Bar resolutions supported by the data layer."""

    M1 = "1m"
    M5 = "5m"
    H1 = "1h"
    D1 = "1d"


_TIMEFRAME_DELTA: dict[Timeframe, timedelta] = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.D1: timedelta(days=1),
}


def timeframe_delta(tf: Timeframe) -> timedelta:
    """Return the canonical ``timedelta`` for a ``Timeframe``."""
    return _TIMEFRAME_DELTA[tf]


@dataclass(frozen=True, slots=True)
class Bar:
    """OHLCV bar at a fixed timestamp.

    Invariants enforced at construction:
    - prices are positive,
    - ``low <= min(open, close)`` and ``high >= max(open, close)``,
    - ``volume >= 0``.
    """

    at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.open <= 0:
            raise ValueError(f"Bar.open must be > 0, got {self.open}")
        if self.high <= 0:
            raise ValueError(f"Bar.high must be > 0, got {self.high}")
        if self.low <= 0:
            raise ValueError(f"Bar.low must be > 0, got {self.low}")
        if self.close <= 0:
            raise ValueError(f"Bar.close must be > 0, got {self.close}")
        if self.volume < 0:
            raise ValueError(f"Bar.volume must be >= 0, got {self.volume}")
        if self.high < max(self.open, self.close):
            raise ValueError(
                f"Bar.high ({self.high}) must be >= max(open={self.open}, close={self.close})"
            )
        if self.low > min(self.open, self.close):
            raise ValueError(
                f"Bar.low ({self.low}) must be <= min(open={self.open}, close={self.close})"
            )
        if self.high < self.low:
            raise ValueError(f"Bar.high ({self.high}) must be >= low ({self.low})")


@dataclass(frozen=True, slots=True)
class Fundamentals:
    """Equity fundamentals consumed by the screener (REQ_F_SCR_001).

    All percentage / ratio fields are ``Decimal`` fractions (e.g.,
    a 4.5 % yield is ``Decimal("0.045")``).
    """

    yield_: Decimal
    payout_ratio: Decimal
    free_cash_flow: Money
    debt_equity: Decimal
    dividend_history_years: int

    def __post_init__(self) -> None:
        if self.yield_ < 0:
            raise ValueError(f"Fundamentals.yield_ must be >= 0, got {self.yield_}")
        if self.payout_ratio < 0:
            raise ValueError(f"Fundamentals.payout_ratio must be >= 0, got {self.payout_ratio}")
        if self.debt_equity < 0:
            raise ValueError(f"Fundamentals.debt_equity must be >= 0, got {self.debt_equity}")
        if self.dividend_history_years < 0:
            raise ValueError(
                "Fundamentals.dividend_history_years must be >= 0, "
                f"got {self.dividend_history_years}"
            )
