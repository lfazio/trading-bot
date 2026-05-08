"""``MarketReplay`` — deterministic tick stream from a data provider.

The replay pre-fetches OHLCV bars for every instrument over the
backtest range, converts each bar to a ``Tick``, and yields the ticks
in ``(timestamp ASC, instrument_id ASC)`` order so replays with the
same seed and inputs produce bit-identical orderings (REQ_SDD_ALG_019,
REQ_F_BCT_001 / REQ_NF_DET_001).

Conversion convention (bar -> tick):
- ``last`` = ``bar.close``
- ``bid`` = ``last x (1 - spread/2)``, ``ask`` = ``last x (1 + spread/2)``
- ``volume`` = ``bar.volume``

``spread_pct`` is read from ``BacktestConfig`` (default 0). Per-instrument
spreads are not modelled at this stage; turbo-specific spreads can be
overlaid by a future step if the backtest universe needs them.

REQ refs:
- REQ_F_BCT_001 — deterministic feed.
- REQ_SDD_ALG_019 — tick ordering (timestamp ASC, instrument_id ASC,
  sequence_id ASC). Within a (ts, iid) pair the sequence id is 0 for
  bar-derived ticks; if higher-frequency feeds arrive later, the
  ordering helper extends naturally.
- REQ_SDS_INT_002 — depends only on the ``MarketDataProvider`` Protocol.
- REQ_SDD_PER_004 — pure-function fast path; >=10k ticks/sec on the
  mock provider.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_system.backtesting.clock import EventClock
from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar, Timeframe
from trading_system.execution.types import Tick
from trading_system.models.instrument import Instrument
from trading_system.result import Err, Ok, Result


@dataclass(slots=True)
class MarketReplay:
    """Deterministic tick generator backed by a list of pre-loaded
    bars sorted in canonical order."""

    _ticks: tuple[Tick, ...] = field(default_factory=tuple)

    @classmethod
    def try_new(  # noqa: PLR0913 — five Protocol-shape inputs + spread + cls
        cls,
        data: MarketDataProvider,
        instruments: tuple[Instrument, ...],
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        spread_pct: Decimal = Decimal(0),
    ) -> Result[MarketReplay, str]:
        """Pre-load bars for every instrument; sort; convert to ticks.

        Returns ``Err("market_replay:fetch_failed:...")`` if any
        provider call fails. ``MarketDataProvider`` is the boundary
        that may surface data errors; the engine layer above must
        not see exceptions (REQ_SDD_ERR_001).
        """
        if start > end:
            return Err(f"market_replay:invalid_range: {start} > {end}")
        if spread_pct < 0:
            return Err(f"market_replay:bad_spread: {spread_pct} < 0")
        all_pairs: list[tuple[datetime, str, Instrument, Bar]] = []
        for instr in instruments:
            res = data.bars(instr, timeframe, start, end)
            match res:
                case Ok(bars):
                    for b in bars:
                        all_pairs.append((b.at, str(instr.id), instr, b))
                case Err(reason):
                    return Err(f"market_replay:fetch_failed:{instr.id}:{reason}")
        # Sort by (timestamp, instrument_id) — deterministic order
        # (REQ_SDD_ALG_019).
        all_pairs.sort(key=lambda x: (x[0], x[1]))
        ticks = tuple(_bar_to_tick(instr, bar, spread_pct) for _, _, instr, bar in all_pairs)
        return Ok(cls(_ticks=ticks))

    def __len__(self) -> int:
        return len(self._ticks)

    def stream(self, clock: EventClock) -> Iterator[Tick]:
        """Yield ticks in canonical order; advance ``clock`` before
        each yield so consumers reading ``clock.now()`` see the tick's
        timestamp."""
        for tick in self._ticks:
            clock.set(tick.at)
            yield tick


def _bar_to_tick(instr: Instrument, bar: Bar, spread_pct: Decimal) -> Tick:
    half = spread_pct / Decimal(2)
    bid = bar.close * (Decimal(1) - half)
    ask = bar.close * (Decimal(1) + half)
    if bid <= 0:
        # Pathological spread on a tiny price — pin bid to last so the
        # Tick invariant (bid > 0) holds; the test universe avoids
        # this corner.
        bid = bar.close
    return Tick(
        at=bar.at,
        instrument_id=instr.id,
        bid=bid,
        ask=ask,
        last=bar.close,
        volume=bar.volume,
    )
