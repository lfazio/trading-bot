"""Wilder's average true range (CR-028 — REQ_F_IND_004 / REQ_SDD_IND_003).

True range:

    TR_i = max(high_i - low_i,
               |high_i - close_{i-1}|,
               |low_i  - close_{i-1}|)

ATR(n) is the Wilder-smoothed TR over ``n`` periods:

    seed ATR = mean(TR_1..TR_n)
    ATR_i    = ((n - 1) * ATR_{i-1} + TR_i) / n

The first TR at index 0 has no previous close — by convention
TR_0 = high_0 - low_0. The seed appears at index ``n`` (after
consuming the first ``n+1`` bars' worth of TR computations).
Indices ``0..n-1`` hold ``None``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from trading_system.data.types import Bar


def atr(bars: Sequence[Bar], n: int = 14) -> tuple[Decimal | None, ...]:
    if n <= 0:
        raise ValueError(f"atr: n must be > 0, got {n}")
    if not bars:
        return ()

    out: list[Decimal | None] = [None] * len(bars)
    if len(bars) < n:
        return tuple(out)

    n_dec = Decimal(n)
    n_minus_one = Decimal(n - 1)

    # Compute true ranges.
    trs: list[Decimal] = []
    for i, bar in enumerate(bars):
        if i == 0:
            tr = bar.high - bar.low
        else:
            prev_close = bars[i - 1].close
            tr = max(
                bar.high - bar.low,
                abs(bar.high - prev_close),
                abs(bar.low - prev_close),
            )
        trs.append(tr)

    # Seed at index n-1 = simple average of TR_0..TR_{n-1}.
    seed = sum(trs[:n], Decimal(0)) / n_dec
    out[n - 1] = seed

    prev = seed
    for i in range(n, len(bars)):
        prev = (n_minus_one * prev + trs[i]) / n_dec
        out[i] = prev

    return tuple(out)
