"""Wilder's relative strength index (CR-028 — REQ_F_IND_004 / REQ_SDD_IND_003).

Formula (Welles Wilder, *New Concepts in Technical Trading Systems*, 1978):

    delta_i = close_i - close_{i-1}
    gain_i  = max(delta_i, 0)
    loss_i  = max(-delta_i, 0)

    seed avg_gain = mean(gain_1..gain_n)
    seed avg_loss = mean(loss_1..loss_n)

    Wilder recurrence:
        avg_gain_i = ((n - 1) * avg_gain_{i-1} + gain_i) / n
        avg_loss_i = ((n - 1) * avg_loss_{i-1} + loss_i) / n

    RS_i  = avg_gain_i / avg_loss_i      (∞ when avg_loss == 0)
    RSI_i = 100 - 100 / (1 + RS_i)       (⇒ 100 when RS == ∞)

The seed appears at index ``n`` (after consuming ``n`` deltas + the
prior close — the warm-up window is ``[0, n]``). Indices ``0..n``
hold ``None``; index ``n+1`` is the first valid RSI? No — the
canonical convention emits the seed at index ``n`` itself, so this
implementation emits ``None`` at ``0..n-1`` and the first RSI at
index ``n``. ``len(output) == len(closes)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def rsi(closes: Sequence[Decimal], n: int = 14) -> tuple[Decimal | None, ...]:
    if n <= 0:
        raise ValueError(f"rsi: n must be > 0, got {n}")
    if not closes:
        return ()
    for v in closes:
        if isinstance(v, float):
            raise TypeError(
                "rsi: closes contain float; Decimal-only (REQ_F_IND_003)"
            )

    out: list[Decimal | None] = [None] * len(closes)
    if len(closes) <= n:
        return tuple(out)

    n_dec = Decimal(n)
    n_minus_one = Decimal(n - 1)
    hundred = Decimal(100)

    # Compute deltas first.
    gains: list[Decimal] = [Decimal(0)] * len(closes)
    losses: list[Decimal] = [Decimal(0)] * len(closes)
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains[i] = delta
        elif delta < 0:
            losses[i] = -delta

    # Seed: simple average of the first n gains/losses (indices 1..n).
    avg_gain = sum(gains[1 : n + 1], Decimal(0)) / n_dec
    avg_loss = sum(losses[1 : n + 1], Decimal(0)) / n_dec

    out[n] = _rsi_from_avgs(avg_gain, avg_loss, hundred)

    for i in range(n + 1, len(closes)):
        avg_gain = (n_minus_one * avg_gain + gains[i]) / n_dec
        avg_loss = (n_minus_one * avg_loss + losses[i]) / n_dec
        out[i] = _rsi_from_avgs(avg_gain, avg_loss, hundred)

    return tuple(out)


def _rsi_from_avgs(
    avg_gain: Decimal, avg_loss: Decimal, hundred: Decimal
) -> Decimal:
    if avg_loss == 0:
        # No down moves in the window ⇒ canonical RSI=100.
        return hundred
    rs = avg_gain / avg_loss
    return hundred - hundred / (Decimal(1) + rs)
