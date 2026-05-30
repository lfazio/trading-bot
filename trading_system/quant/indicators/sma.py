"""Simple moving average (CR-028 — REQ_F_IND_001 / REQ_SDD_IND_002)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def sma(closes: Sequence[Decimal], n: int) -> tuple[Decimal | None, ...]:
    """``n``-period simple moving average.

    Returns a parallel tuple of the same length as ``closes``:
    indices ``0..n-2`` hold ``None`` (insufficient history);
    index ``n-1`` and onwards hold the mean of the trailing
    ``n`` closes.

    Decimal-only arithmetic — passing ``float`` raises ``TypeError``
    so callers see the precision violation at the boundary
    (REQ_F_IND_003 / REQ_SDD_IND_002).
    """
    if n <= 0:
        raise ValueError(f"sma: n must be > 0, got {n}")
    out: list[Decimal | None] = []
    n_dec = Decimal(n)
    for i in range(len(closes)):
        if i + 1 < n:
            out.append(None)
            continue
        window = closes[i - n + 1 : i + 1]
        # Decimal-only guard: float operands surface here as TypeError
        # via the Decimal addition path.
        for v in window:
            if isinstance(v, float):
                raise TypeError(
                    f"sma: closes contain float at index {i}; Decimal-only "
                    f"(REQ_F_IND_003)"
                )
        total = sum(window, Decimal(0))
        out.append(total / n_dec)
    return tuple(out)
