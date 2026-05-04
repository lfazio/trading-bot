"""Risk metrics — drawdown, portfolio vol, realized correlation.

Pure ``Decimal`` arithmetic; no float, no I/O. Each function is
total over its inputs (returns ``None`` instead of raising on
degenerate cases).

REQ refs:
- REQ_SDD_ALG_005 — drawdown formula.
- REQ_SDD_ALG_008 — Pearson correlation, 60-day default window.
- REQ_SDD_ALG_009 — annualized portfolio vol; Phase 5 cap 12 %, Phase 6 cap 8 %.
- REQ_SDD_TYP_001 — Decimal everywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from trading_system.models.flow import EquityPoint

TRADING_DAYS_PER_YEAR = Decimal(252)
_MIN_CORRELATION_POINTS = 2


def drawdown_now(curve: Sequence[EquityPoint]) -> Decimal:
    """Current after-tax drawdown from running peak (REQ_SDD_ALG_005).

    Returns ``Decimal(0)`` when:
    - ``curve`` is empty,
    - the peak is non-positive (degenerate),
    - the current equity exceeds the peak (clamps to 0 instead of
      returning a negative drawdown).
    """
    if not curve:
        return Decimal(0)
    peak = max(p.equity_after_tax.amount for p in curve)
    cur = curve[-1].equity_after_tax.amount
    if peak <= 0:
        return Decimal(0)
    drawdown = Decimal(1) - cur / peak
    if drawdown < Decimal(0):
        return Decimal(0)
    return drawdown


def portfolio_vol_ann(curve: Sequence[EquityPoint], window: int) -> Decimal | None:
    """Annualized realized volatility of the after-tax equity curve
    over the last ``window`` returns. Returns ``None`` when fewer
    than ``window + 1`` points are available or any reference equity
    is non-positive (REQ_SDD_ALG_009)."""
    if window <= 0 or len(curve) < window + 1:
        return None
    rets: list[Decimal] = []
    start = len(curve) - window
    for i in range(start, len(curve)):
        prev = curve[i - 1].equity_after_tax.amount
        cur = curve[i].equity_after_tax.amount
        if prev <= 0:
            return None
        rets.append((cur - prev) / prev)
    mean = sum(rets, start=Decimal(0)) / Decimal(window)
    variance = sum((r - mean) ** 2 for r in rets) / Decimal(window)
    daily_std = variance.sqrt()
    return daily_std * TRADING_DAYS_PER_YEAR.sqrt()


def realized_correlation(
    series_a: Sequence[Decimal], series_b: Sequence[Decimal]
) -> Decimal | None:
    """Pearson correlation between two equal-length return series
    (REQ_SDD_ALG_008). Returns ``None`` on:

    - mismatched lengths,
    - fewer than two points,
    - either series having zero variance (degenerate).
    """
    if len(series_a) != len(series_b):
        return None
    n = len(series_a)
    if n < _MIN_CORRELATION_POINTS:
        return None
    mean_a = sum(series_a, start=Decimal(0)) / Decimal(n)
    mean_b = sum(series_b, start=Decimal(0)) / Decimal(n)
    cov = sum(
        (a - mean_a) * (b - mean_b) for a, b in zip(series_a, series_b, strict=True)
    ) / Decimal(n)
    var_a = sum((a - mean_a) ** 2 for a in series_a) / Decimal(n)
    var_b = sum((b - mean_b) ** 2 for b in series_b) / Decimal(n)
    if var_a <= 0 or var_b <= 0:
        return None
    return cov / (var_a.sqrt() * var_b.sqrt())
