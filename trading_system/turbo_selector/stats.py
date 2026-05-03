"""Statistics helpers — pure ``Decimal`` arithmetic over bar series.

REQ refs: REQ_SDD_TYP_001 (Decimal everywhere), REQ_F_TRB_002 (vol +
liquidity feed the filter), REQ_F_BCT_001 (deterministic).

Float is forbidden in domain code. Decimal supports ``sqrt`` natively
in the default arithmetic context, which is enough for the
volatility / volume aggregations used by the turbo filter.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

# Trading days per year — the SDD is implicit on this; 252 is the
# industry default for daily-bar annualization.
TRADING_DAYS_PER_YEAR = Decimal(252)


def realized_vol(closes: Sequence[Decimal], window: int) -> Decimal | None:
    """Annualized realized volatility computed from the last ``window``
    simple returns of ``closes``.

    Returns ``None`` when fewer than ``window + 1`` closes are
    available (one extra is needed to derive the first return) or
    when any reference close is non-positive.
    """
    if window <= 0 or len(closes) < window + 1:
        return None
    rets: list[Decimal] = []
    start = len(closes) - window
    for i in range(start, len(closes)):
        prev = closes[i - 1]
        cur = closes[i]
        if prev <= 0:
            return None
        rets.append((cur - prev) / prev)
    mean = sum(rets, start=Decimal(0)) / Decimal(window)
    sq_dev = sum((r - mean) ** 2 for r in rets)
    variance = sq_dev / Decimal(window)
    daily_std = variance.sqrt()
    return daily_std * TRADING_DAYS_PER_YEAR.sqrt()


def avg_volume(volumes: Sequence[Decimal], window: int) -> Decimal | None:
    """Simple average of the last ``window`` volume values; ``None``
    when fewer than ``window`` values are available."""
    if window <= 0 or len(volumes) < window:
        return None
    tail = list(volumes[-window:])
    return sum(tail, start=Decimal(0)) / Decimal(window)
