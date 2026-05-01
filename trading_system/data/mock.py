"""``MockMarketDataProvider`` — deterministic in-process market data.

Generates OHLCV bars via a seeded RNG so that identical
``(seed, instrument.id, timeframe, start)`` tuples produce identical
output (REQ_SDD_TST_002, REQ_F_BCT_001, REQ_NF_DET_001). Fundamentals
and dividends are explicitly registered by the test or backtest
harness — they are not random data.

The mock satisfies the conformance baseline for ``MarketDataProvider``;
any future live provider MUST pass the same conformance suite
(REQ_SDS_INT_002).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_system.data.types import Bar, Fundamentals, Timeframe, timeframe_delta
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import Instrument
from trading_system.models.trading import Dividend
from trading_system.result import Err, Ok, Result

_DEFAULT_OPEN = Decimal("100.0000")
_PRICE_QUANT = Decimal("0.0001")
_VOL_DAILY = 0.012  # ~1.2% per-bar standard deviation (float; deterministic via seed)
_SPREAD_FRAC = 0.005


@dataclass(slots=True)
class MockMarketDataProvider:
    """Deterministic in-process market-data provider.

    Generated bars start at ``_DEFAULT_OPEN`` and follow a seeded random
    walk. The walk is reset for each ``bars`` call by mixing the seed
    with ``(instrument.id, timeframe, start)``; this keeps bar series
    pure-functional (same inputs → same outputs) at the cost of
    discontinuity across non-overlapping ranges, which matches the
    deterministic-mock contract documented in the SDD.
    """

    seed: int
    _fundamentals: dict[InstrumentId, Fundamentals] = field(default_factory=dict)
    _dividends: dict[tuple[InstrumentId, int], list[Dividend]] = field(default_factory=dict)
    _as_of: datetime | None = None

    # ------------------------------------------------------------------
    # Test harness: register reference data
    # ------------------------------------------------------------------

    def set_now(self, now: datetime) -> None:
        """Configure the "now" used by ``latest`` (REQ_SDS_ARC_006 keeps
        wall-clock out of providers)."""
        self._as_of = now

    def register_fundamentals(
        self, instrument_id: InstrumentId, fundamentals: Fundamentals
    ) -> None:
        self._fundamentals[instrument_id] = fundamentals

    def register_dividend(self, dividend: Dividend) -> None:
        key = (dividend.instrument, dividend.ex_date.year)
        self._dividends.setdefault(key, []).append(dividend)
        self._dividends[key].sort(key=lambda d: d.ex_date)

    # ------------------------------------------------------------------
    # MarketDataProvider Protocol
    # ------------------------------------------------------------------

    def bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Result[list[Bar], str]:
        if start > end:
            return Err(f"data:invalid_range: start {start.isoformat()} after end {end.isoformat()}")
        rng = random.Random(_mix_seed(self.seed, instrument.id, timeframe, start))
        delta = timeframe_delta(timeframe)
        cursor = start
        prev_close = _DEFAULT_OPEN
        bars: list[Bar] = []
        while cursor <= end:
            move = Decimal(str(rng.gauss(0, _VOL_DAILY)))
            new_close = (prev_close * (Decimal(1) + move)).quantize(_PRICE_QUANT)
            if new_close <= 0:
                new_close = _PRICE_QUANT  # floor against numerical extremes
            bars.append(_make_bar(cursor, prev_close, new_close, rng))
            prev_close = new_close
            cursor += delta
        return Ok(bars)

    def latest(self, instrument: Instrument) -> Result[Bar, str]:
        if self._as_of is None:
            return Err("data:no_as_of: call set_now() before latest()")
        result = self.bars(instrument, Timeframe.D1, self._as_of, self._as_of)
        if isinstance(result, Err):
            return result
        bars = result.value
        if not bars:
            return Err(f"data:not_found: no bar at {self._as_of.isoformat()}")
        return Ok(bars[-1])

    def dividends(self, instrument: Instrument, year: int) -> Result[list[Dividend], str]:
        return Ok(list(self._dividends.get((instrument.id, year), [])))

    def fundamentals(self, instrument: Instrument) -> Result[Fundamentals, str]:
        f = self._fundamentals.get(instrument.id)
        if f is None:
            return Err(f"data:not_found: no fundamentals registered for {instrument.id}")
        return Ok(f)


def _mix_seed(seed: int, instrument_id: InstrumentId, timeframe: Timeframe, start: datetime) -> str:
    """Stable string seed for ``random.Random``. Same inputs always produce
    the same stream (REQ_SDD_TST_002)."""
    return f"{seed}|{instrument_id}|{timeframe.value}|{start.isoformat()}"


def _make_bar(at: datetime, prev_close: Decimal, new_close: Decimal, rng: random.Random) -> Bar:
    """Build a Bar around ``new_close`` with deterministic noise on
    high / low / volume drawn from ``rng``."""
    high_noise = Decimal(str(abs(rng.gauss(0, _SPREAD_FRAC))))
    low_noise = Decimal(str(abs(rng.gauss(0, _SPREAD_FRAC))))
    high = (max(prev_close, new_close) * (Decimal(1) + high_noise)).quantize(_PRICE_QUANT)
    low = (min(prev_close, new_close) * (Decimal(1) - low_noise)).quantize(_PRICE_QUANT)
    if low <= 0:
        low = _PRICE_QUANT
    volume = Decimal(int(rng.uniform(10_000, 100_000)))
    return Bar(at=at, open=prev_close, high=high, low=low, close=new_close, volume=volume)
