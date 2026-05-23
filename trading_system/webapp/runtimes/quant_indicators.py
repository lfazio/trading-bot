"""Lightweight quant indicators for the dashboard panel.

Pure functions over bar series + equity series — no I/O, no
external dependencies. Consumed by the
``RuntimePaperStateReader`` so the operator sees useful
diagnostics next to the live equity.

Indicators surfaced:
- SMA-20 / SMA-50 of close prices
- Realized volatility (last 20 bars, annualised %)
- Total return % vs starting close
- Drawdown from peak %
- Sharpe ratio over the recorded equity series
- Trend signal — ``"up"`` when SMA-20 > SMA-50, ``"down"`` when
  inverted, ``"flat"`` when too few bars
- Latest market regime (pure function of bars + RegimeConfig).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


TrendSignal = Literal["up", "down", "flat", "n/a"]


@dataclass(frozen=True, slots=True)
class QuantIndicators:
    """Snapshot of computed quant indicators for one paper session."""

    sma_20: Decimal | None
    sma_50: Decimal | None
    realized_vol_pct: Decimal | None
    total_return_pct: Decimal | None
    drawdown_pct: Decimal | None
    sharpe_ratio: Decimal | None
    trend_signal: TrendSignal
    regime: str  # "BULL" / "BEAR" / "SIDEWAYS" / "HIGH_VOL" / "n/a"


def _sma(values: list[Decimal], window: int) -> Decimal | None:
    if len(values) < window or window <= 0:
        return None
    head = values[-window:]
    return (sum(head, start=Decimal("0")) / Decimal(window)).quantize(
        Decimal("0.0001")
    )


def _realized_vol_pct(closes: list[Decimal], window: int = 20) -> Decimal | None:
    """Annualised realized volatility of log returns over the last
    ``window`` bars, expressed in percent."""
    if len(closes) < window + 1:
        return None
    tail = closes[-(window + 1):]
    log_returns: list[float] = []
    for i in range(1, len(tail)):
        prev = float(tail[i - 1])
        cur = float(tail[i])
        if prev <= 0 or cur <= 0:
            return None
        log_returns.append(math.log(cur / prev))
    n = len(log_returns)
    if n < 2:
        return None
    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    sigma = math.sqrt(variance)
    # Annualise assuming the simulator's step ≈ 1 trading day (252).
    annualised = sigma * math.sqrt(252)
    return Decimal(str(annualised * 100)).quantize(Decimal("0.01"))


def _drawdown_pct(equity_values: list[Decimal]) -> Decimal | None:
    if not equity_values:
        return None
    peak = equity_values[0]
    worst = Decimal("0")
    for v in equity_values:
        if v > peak:
            peak = v
        if peak <= 0:
            continue
        dd = (peak - v) / peak * Decimal("100")
        if dd > worst:
            worst = dd
    return worst.quantize(Decimal("0.01"))


def _sharpe_ratio(equity_values: list[Decimal]) -> Decimal | None:
    """Per-step Sharpe ratio (no annualisation — kept comparable
    across sessions of varying length). Returns None on too few
    samples."""
    if len(equity_values) < 3:
        return None
    returns: list[float] = []
    for i in range(1, len(equity_values)):
        prev = float(equity_values[i - 1])
        cur = float(equity_values[i])
        if prev <= 0:
            return None
        returns.append((cur - prev) / prev)
    if not returns:
        return None
    mean = sum(returns) / len(returns)
    if len(returns) < 2:
        return None
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sigma = math.sqrt(variance)
    if sigma <= 0:
        return None
    sharpe = mean / sigma
    return Decimal(str(sharpe)).quantize(Decimal("0.0001"))


def compute_indicators(
    closes: list[Decimal],
    equity_amounts: list[Decimal],
    *,
    regime: str = "n/a",
) -> QuantIndicators:
    """Compute the full indicator snapshot. Empty inputs ⇒ all
    fields fall back to ``None`` / sentinels."""
    sma_20 = _sma(closes, 20)
    sma_50 = _sma(closes, 50)
    vol = _realized_vol_pct(closes, window=20)
    if closes:
        first = closes[0]
        last = closes[-1]
        total_return = (
            ((last - first) / first * Decimal("100")).quantize(Decimal("0.01"))
            if first > 0
            else None
        )
    else:
        total_return = None
    dd = _drawdown_pct(equity_amounts)
    sharpe = _sharpe_ratio(equity_amounts)
    if sma_20 is None or sma_50 is None:
        trend: TrendSignal = "n/a"
    elif sma_20 > sma_50:
        trend = "up"
    elif sma_20 < sma_50:
        trend = "down"
    else:
        trend = "flat"
    return QuantIndicators(
        sma_20=sma_20,
        sma_50=sma_50,
        realized_vol_pct=vol,
        total_return_pct=total_return,
        drawdown_pct=dd,
        sharpe_ratio=sharpe,
        trend_signal=trend,
        regime=regime,
    )
