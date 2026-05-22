"""Property-based tests for mock data determinism — REQ_TP_STR_002.

REQ_F_DAT_005 / REQ_NF_REP_001 — two ``MockMarketDataProvider.bars``
calls with identical ``(seed, instrument, timeframe, start, end)``
tuples SHALL produce identical Bar sequences. The provider is the
test-mode replay-determinism guarantee for every backtest.

Properties verified:

- Reproducibility — identical inputs ⇒ identical bars.
- Independence-by-seed — different seeds ⇒ different bars
  (probabilistically; we test that at least one bar differs).
- Pure-function shape — bars are deterministic from the
  ``(instrument.id, timeframe, start)`` tuple only; the
  provider's internal state can't leak across calls.
- Length — the number of bars equals the number of timeframe
  ticks in the closed interval ``[start, end]``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import Instrument, InstrumentClass
from trading_system.models.money import Currency
from trading_system.result import Ok


def _stock(symbol: str = "ASML") -> Instrument:
    return Instrument(
        id=InstrumentId(f"{symbol}.AS"),
        symbol=symbol,
        exchange="AS",
        cls=InstrumentClass.STOCK,
        currency=Currency.EUR,
    )


_SEEDS = st.integers(min_value=0, max_value=10_000)
_DAYS = st.integers(min_value=1, max_value=30)


def _start(day_of_2026: int) -> datetime:
    return datetime(2026, 1, day_of_2026, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Same seed → same bars
# ---------------------------------------------------------------------------


@given(seed=_SEEDS, days=_DAYS)
@settings(max_examples=50)
def test_same_seed_produces_identical_bars(seed: int, days: int) -> None:
    """REQ_F_DAT_005 / REQ_NF_REP_001 — identical inputs SHALL
    produce identical bars across two independent provider
    instances."""
    instr = _stock()
    start = _start(1)
    end = start + timedelta(days=days)
    a = MockMarketDataProvider(seed=seed)
    b = MockMarketDataProvider(seed=seed)
    ra = a.bars(instr, Timeframe.D1, start, end)
    rb = b.bars(instr, Timeframe.D1, start, end)
    assert isinstance(ra, Ok) and isinstance(rb, Ok)
    assert ra.value == rb.value, (
        f"seed {seed} not deterministic across instances"
    )


# ---------------------------------------------------------------------------
# Different seeds → different bars
# ---------------------------------------------------------------------------


@given(
    seed_a=_SEEDS,
    seed_b=_SEEDS,
)
@settings(max_examples=50)
def test_different_seeds_produce_different_bars(
    seed_a: int, seed_b: int
) -> None:
    """When ``seed_a != seed_b``, at least one bar SHALL differ.
    Sanity check that the seed actually mixes into the RNG state."""
    if seed_a == seed_b:
        return  # trivially pass — equal seeds is a separate property
    instr = _stock()
    start = _start(1)
    end = start + timedelta(days=10)
    a = MockMarketDataProvider(seed=seed_a)
    b = MockMarketDataProvider(seed=seed_b)
    ra = a.bars(instr, Timeframe.D1, start, end)
    rb = b.bars(instr, Timeframe.D1, start, end)
    assert isinstance(ra, Ok) and isinstance(rb, Ok)
    assert ra.value != rb.value, (
        f"seeds {seed_a}/{seed_b} produced identical bars"
    )


# ---------------------------------------------------------------------------
# Repeated calls within one instance are deterministic
# ---------------------------------------------------------------------------


@given(seed=_SEEDS, days=_DAYS)
@settings(max_examples=50)
def test_repeated_call_within_instance_is_pure(
    seed: int, days: int
) -> None:
    """Calling ``bars`` twice on the SAME provider with the SAME
    inputs SHALL produce identical output — the provider's
    internal state SHALL NOT leak across calls (the per-call RNG
    is re-seeded from the mixed seed every time)."""
    instr = _stock()
    start = _start(1)
    end = start + timedelta(days=days)
    p = MockMarketDataProvider(seed=seed)
    r1 = p.bars(instr, Timeframe.D1, start, end)
    r2 = p.bars(instr, Timeframe.D1, start, end)
    assert isinstance(r1, Ok) and isinstance(r2, Ok)
    assert r1.value == r2.value


# ---------------------------------------------------------------------------
# Bar count matches the interval
# ---------------------------------------------------------------------------


@given(seed=_SEEDS, days=_DAYS)
@settings(max_examples=30)
def test_bar_count_matches_interval(seed: int, days: int) -> None:
    """For a D1 timeframe over ``days``, the bar count SHALL be
    ``days + 1`` (closed interval ``[start, start + days]``
    inclusive)."""
    instr = _stock()
    start = _start(1)
    end = start + timedelta(days=days)
    p = MockMarketDataProvider(seed=seed)
    res = p.bars(instr, Timeframe.D1, start, end)
    assert isinstance(res, Ok)
    assert len(res.value) == days + 1, (
        f"expected {days + 1} bars, got {len(res.value)}"
    )


# ---------------------------------------------------------------------------
# Bar prices stay positive
# ---------------------------------------------------------------------------


@given(seed=_SEEDS, days=_DAYS)
@settings(max_examples=30)
def test_bar_prices_remain_positive(seed: int, days: int) -> None:
    """REQ_SDD_DAT_001 — bar prices SHALL be > 0. The mock walks
    with a floor (``_PRICE_QUANT``) against numerical extremes."""
    instr = _stock()
    start = _start(1)
    end = start + timedelta(days=days)
    p = MockMarketDataProvider(seed=seed)
    res = p.bars(instr, Timeframe.D1, start, end)
    assert isinstance(res, Ok)
    for bar in res.value:
        assert bar.open > Decimal(0)
        assert bar.high > Decimal(0)
        assert bar.low > Decimal(0)
        assert bar.close > Decimal(0)
        # OHLC invariants.
        assert bar.high >= bar.open
        assert bar.high >= bar.close
        assert bar.low <= bar.open
        assert bar.low <= bar.close
