"""``RegimeDetector`` — pure function over bars + ``RegimeConfig``.

Tie-break order ``HIGH_VOL > BEAR > BULL > SIDEWAYS`` is exposed as
the public module-level constant ``RULE_ORDER`` so tests can assert on
it directly without instrumenting ``evaluate()``
(REQ_F_RGM_003 / REQ_SDD_RGM_001).

REQ refs: REQ_F_RGM_001..003, REQ_NF_RGM_001, REQ_SDD_RGM_001,
REQ_SDD_RGM_002.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from trading_system.data.types import Bar
from trading_system.models.phase import MarketRegime
from trading_system.regime.config import RegimeConfig
from trading_system.result import Err, Ok, Result

# Public tie-break ordering (REQ_F_RGM_003 / REQ_SDD_RGM_001).
# Tests assert on this constant directly.
RULE_ORDER: tuple[str, ...] = ("HIGH_VOL", "BEAR", "BULL", "SIDEWAYS")


@dataclass(slots=True)
class RegimeDetector:
    """Classify ``MarketRegime`` from a bar history using the
    MA-crossover + vol-band rule documented in REQ_F_RGM_002.

    The detector is a pure function of ``(bars, config)`` — no
    portfolio, broker, or clock references (REQ_SDS_RGM_002).
    """

    config: RegimeConfig

    def evaluate(self, bars: Sequence[Bar]) -> Result[MarketRegime, str]:
        if len(bars) < self.config.ma_long:
            return Err(
                f"regime:insufficient_bars:{len(bars)}<{self.config.ma_long}"
            )
        closes = tuple(b.close for b in bars)
        ma_s = _mean(closes[-self.config.ma_short:])
        ma_l = _mean(closes[-self.config.ma_long:])
        latest_price = closes[-1]

        # Realised volatility series over the trailing ``vol_window``
        # periods, then the most recent value vs. the configured
        # percentiles.
        vol_series = _rolling_volatility(closes, self.config.vol_window)
        if not vol_series:
            # Not enough history for a vol series; fall through to
            # MA-only classification.
            return Ok(MarketRegime.BULL if ma_s >= ma_l else MarketRegime.BEAR)
        vol_today = vol_series[-1]
        vol_high = _percentile(vol_series, self.config.vol_high_percentile)
        vol_low = _percentile(vol_series, self.config.vol_low_percentile)

        # Tie-break order: HIGH_VOL > BEAR > BULL > SIDEWAYS
        # (REQ_F_RGM_003 / REQ_SDD_RGM_001). Apply rules in this order;
        # the first match wins.
        if vol_today > vol_high:
            return Ok(MarketRegime.HIGH_VOL)
        if ma_s < ma_l:
            return Ok(MarketRegime.BEAR)
        if ma_s > ma_l and vol_today < vol_low:
            return Ok(MarketRegime.BULL)
        if abs(ma_s - ma_l) < self.config.sideways_threshold * latest_price:
            return Ok(MarketRegime.SIDEWAYS)
        # Default fall-through (REQ_SDD_RGM_002): BULL when MA-up,
        # else BEAR. Covers the mid-band case where vol is neither
        # high nor low and the MAs aren't tightly converged.
        return Ok(MarketRegime.BULL if ma_s >= ma_l else MarketRegime.BEAR)


def _mean(xs: Sequence[Decimal]) -> Decimal:
    if not xs:
        return Decimal(0)
    return sum(xs, start=Decimal(0)) / Decimal(len(xs))


def _rolling_volatility(closes: Sequence[Decimal], window: int) -> tuple[Decimal, ...]:
    """Compute the realised volatility series — standard deviation of
    log-returns over each trailing ``window``. Returns the empty tuple
    when there isn't enough history for at least one window."""
    if len(closes) < window + 1:
        return ()
    # Log-returns via ``ln(close[i] / close[i-1])``; we use the
    # ``Decimal.ln()`` method to keep precision and avoid floats.
    log_returns: list[Decimal] = []
    for i in range(1, len(closes)):
        prev, curr = closes[i - 1], closes[i]
        if prev <= 0 or curr <= 0:
            raise ValueError(
                f"regime: close prices must be > 0 (got prev={prev}, curr={curr})"
            )
        log_returns.append((curr / prev).ln())
    out: list[Decimal] = []
    for i in range(window - 1, len(log_returns)):
        chunk = log_returns[i - window + 1 : i + 1]
        out.append(_stddev(chunk))
    return tuple(out)


def _stddev(xs: Sequence[Decimal]) -> Decimal:
    if len(xs) < 2:
        return Decimal(0)
    mean = sum(xs, start=Decimal(0)) / Decimal(len(xs))
    variance_sum = sum((x - mean) * (x - mean) for x in xs)
    variance = variance_sum / Decimal(len(xs) - 1)
    return variance.sqrt()


def _percentile(xs: Sequence[Decimal], q: Decimal) -> Decimal:
    """Linear-interpolation percentile. ``q`` is in [0, 1]."""
    if not xs:
        return Decimal(0)
    if q <= 0:
        return min(xs)
    if q >= 1:
        return max(xs)
    sorted_xs = sorted(xs)
    pos = q * Decimal(len(sorted_xs) - 1)
    lo = int(pos)
    hi = lo + 1
    if hi >= len(sorted_xs):
        return sorted_xs[lo]
    frac = pos - Decimal(lo)
    return sorted_xs[lo] + frac * (sorted_xs[hi] - sorted_xs[lo])
