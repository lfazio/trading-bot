"""On-balance volume (CR-028 — REQ_F_IND_001 / REQ_SDD_IND_002).

Cumulative momentum confirmation signal:

    OBV_0 = 0
    OBV_i = OBV_{i-1} + volume_i        if close_i > close_{i-1}
            OBV_{i-1} - volume_i        if close_i < close_{i-1}
            OBV_{i-1}                   if close_i == close_{i-1}

No warm-up — returns ``Decimal`` at every index (no ``None``).
``len(output) == len(bars)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from trading_system.data.types import Bar


def obv(bars: Sequence[Bar]) -> tuple[Decimal, ...]:
    if not bars:
        return ()
    out: list[Decimal] = [Decimal(0)]
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        close = bars[i].close
        volume = bars[i].volume
        if close > prev_close:
            out.append(out[-1] + volume)
        elif close < prev_close:
            out.append(out[-1] - volume)
        else:
            out.append(out[-1])
    return tuple(out)
