"""Wilder's average directional index (CR-028 — REQ_F_IND_004 / REQ_SDD_IND_003).

Directional movements:

    up_move_i   = high_i - high_{i-1}
    down_move_i = low_{i-1} - low_i

    +DM_i = up_move_i   if up_move_i > down_move_i and up_move_i > 0 else 0
    -DM_i = down_move_i if down_move_i > up_move_i and down_move_i > 0 else 0

True range (same as ATR):

    TR_i = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)

Wilder smoothing of +DM / -DM / TR over ``n`` periods:

    seed = mean of first n values
    smoothed_i = ((n - 1) * smoothed_{i-1} + raw_i) / n

Directional indicators:

    +DI_i = 100 * smoothed_+DM_i / smoothed_TR_i
    -DI_i = 100 * smoothed_-DM_i / smoothed_TR_i

Directional movement index:

    DX_i = 100 * |+DI_i - -DI_i| / (+DI_i + -DI_i)

ADX is the Wilder smooth of DX over a SECOND ``n``-period window —
so the first ADX value appears at index ``2n - 1`` (the +DI/-DI
warm-up consumes ``n`` periods + the DX smoothing consumes another
``n``). Indices ``0..2n-2`` hold ``None``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from trading_system.data.types import Bar


def adx(bars: Sequence[Bar], n: int = 14) -> tuple[Decimal | None, ...]:
    if n <= 0:
        raise ValueError(f"adx: n must be > 0, got {n}")
    if not bars:
        return ()

    out: list[Decimal | None] = [None] * len(bars)
    if len(bars) < 2 * n:
        return tuple(out)

    n_dec = Decimal(n)
    n_minus_one = Decimal(n - 1)
    hundred = Decimal(100)

    # Per-bar raw directional movements + true ranges. Index 0 has
    # no previous bar — pad with zero / first-bar range.
    plus_dm: list[Decimal] = [Decimal(0)]
    minus_dm: list[Decimal] = [Decimal(0)]
    trs: list[Decimal] = [bars[0].high - bars[0].low]
    for i in range(1, len(bars)):
        up_move = bars[i].high - bars[i - 1].high
        down_move = bars[i - 1].low - bars[i].low
        plus_dm.append(
            up_move if up_move > down_move and up_move > 0 else Decimal(0)
        )
        minus_dm.append(
            down_move if down_move > up_move and down_move > 0 else Decimal(0)
        )
        prev_close = bars[i - 1].close
        trs.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - prev_close),
                abs(bars[i].low - prev_close),
            )
        )

    # Wilder seed at index n: sum of the first n raw values (indices 1..n).
    # We use 1..n+1 (Python slice) to skip the index-0 placeholder.
    smoothed_plus = sum(plus_dm[1 : n + 1], Decimal(0))
    smoothed_minus = sum(minus_dm[1 : n + 1], Decimal(0))
    smoothed_tr = sum(trs[1 : n + 1], Decimal(0))

    # +DI / -DI / DX accumulator across the second n-period window.
    # First DX appears at index n; ADX seed = mean of DX_n..DX_{2n-1}.
    dx_window: list[Decimal] = []

    def dx_at_current() -> Decimal:
        if smoothed_tr == 0:
            return Decimal(0)
        plus_di = hundred * smoothed_plus / smoothed_tr
        minus_di = hundred * smoothed_minus / smoothed_tr
        denom = plus_di + minus_di
        if denom == 0:
            return Decimal(0)
        return hundred * abs(plus_di - minus_di) / denom

    dx_window.append(dx_at_current())  # DX_n

    for i in range(n + 1, 2 * n):
        # Wilder smooth (subtract-and-add convention for Decimal stability).
        smoothed_plus = smoothed_plus - smoothed_plus / n_dec + plus_dm[i]
        smoothed_minus = smoothed_minus - smoothed_minus / n_dec + minus_dm[i]
        smoothed_tr = smoothed_tr - smoothed_tr / n_dec + trs[i]
        dx_window.append(dx_at_current())

    # ADX seed at index 2n-1.
    adx_value = sum(dx_window, Decimal(0)) / n_dec
    out[2 * n - 1] = adx_value

    # Continue: each subsequent ADX uses the +DI / -DI / DX recurrence.
    for i in range(2 * n, len(bars)):
        smoothed_plus = smoothed_plus - smoothed_plus / n_dec + plus_dm[i]
        smoothed_minus = smoothed_minus - smoothed_minus / n_dec + minus_dm[i]
        smoothed_tr = smoothed_tr - smoothed_tr / n_dec + trs[i]
        dx_i = dx_at_current()
        adx_value = (n_minus_one * adx_value + dx_i) / n_dec
        out[i] = adx_value

    return tuple(out)
