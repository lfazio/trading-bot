"""CR-028 / TC_IND_001..010 — technical-indicator library.

REQ refs:
- REQ_F_IND_001..006 (closed function set, return-shape contract,
  Decimal-only discipline, Wilder smoothing, VolatilityIndexProvider,
  determinism)
- REQ_NF_IND_001 (runtime-safe import)
- REQ_SDD_IND_001..005 (packaging + return-shape + recurrence +
  Protocol + determinism conformance)
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.data.types import Bar
from trading_system.data.volatility_index import (
    VolatilityIndexProvider,
    YFinanceVolatilityIndexProvider,
)
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.quant.indicators import adx, atr, obv, rsi, sma
from trading_system.result import Err, Ok, Result


_T0 = datetime(2026, 5, 30, 12, tzinfo=UTC)


def _bar(*, h: str, l: str, c: str, o: str | None = None, v: str = "1000") -> Bar:
    return Bar(
        at=_T0,
        open=Decimal(o or c),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal(v),
    )


# ---------------------------------------------------------------------------
# TC_IND_001 — SMA golden values
# ---------------------------------------------------------------------------


def test_sma_golden_values_n3():
    """REQ_F_IND_001 / REQ_F_IND_002 / REQ_SDD_IND_002 — SMA returns
    parallel tuple; warmup positions hold None; n-1 onwards = mean."""
    closes = [Decimal(v) for v in (10, 12, 14, 16, 18, 20)]
    result = sma(closes, n=3)
    assert len(result) == len(closes)
    assert result[0] is None
    assert result[1] is None
    assert result[2] == Decimal(12)
    assert result[3] == Decimal(14)
    assert result[4] == Decimal(16)
    assert result[5] == Decimal(18)


def test_sma_rejects_non_positive_n():
    with pytest.raises(ValueError, match="must be > 0"):
        sma([Decimal(1)], n=0)


# ---------------------------------------------------------------------------
# TC_IND_002 — RSI Wilder smoothing
# ---------------------------------------------------------------------------


def test_rsi_monotone_up_yields_100_at_warmup_boundary():
    """REQ_F_IND_004 / REQ_SDD_IND_003 — every bar gains; no losses;
    avg_loss == 0 ⇒ RSI=100."""
    closes = [Decimal(100 + i) for i in range(20)]
    result = rsi(closes, n=14)
    assert len(result) == 20
    # Warmup: indices 0..13 hold None (n consumed: 1..14 deltas).
    for i in range(14):
        assert result[i] is None
    assert result[14] == Decimal(100)
    # Subsequent recurrence with avg_loss still 0 ⇒ stays 100.
    assert result[15] == Decimal(100)


def test_rsi_mixed_bars_near_50_at_boundary():
    """REQ_F_IND_004 — alternating gains/losses ⇒ RSI ≈ 50."""
    # 14 alternating +1 / -1 closes ⇒ avg_gain ≈ avg_loss ⇒ RSI ≈ 50.
    base = Decimal(100)
    closes = [base]
    for i in range(1, 16):
        closes.append(closes[-1] + (Decimal(1) if i % 2 else Decimal(-1)))
    result = rsi(closes, n=14)
    boundary = result[14]
    assert boundary is not None
    assert Decimal("45") < boundary < Decimal("55")


# ---------------------------------------------------------------------------
# TC_IND_003 — ATR Wilder seed + recurrence
# ---------------------------------------------------------------------------


def test_atr_seed_and_warmup():
    """REQ_F_IND_004 / REQ_SDD_IND_003 — ATR seed appears at n-1
    as simple average of the first n true ranges."""
    # Hand-computed: each bar carries h-l = 5; first TR (i=0) = 5,
    # rest also TR=5 (close == h-1 in this fixture).
    bars = [_bar(h="105", l="100", c="103") for _ in range(6)]
    result = atr(bars, n=3)
    assert len(result) == 6
    assert result[0] is None
    assert result[1] is None
    # Seed at index n-1 = 2 = mean of TR_0..TR_2 = 5.
    assert result[2] == Decimal(5)


def test_atr_recurrence_matches_wilder():
    """REQ_SDD_IND_003 — Wilder recurrence:
    `new = ((n-1) * prev + current) / n`."""
    bars = [
        _bar(h="105", l="100", c="103"),
        _bar(h="106", l="101", c="104"),
        _bar(h="108", l="102", c="106"),
        _bar(h="110", l="105", c="108"),  # TR = 5 (h-l)
        _bar(h="112", l="107", c="110"),  # TR = 5
    ]
    n = 3
    result = atr(bars, n=n)
    # Seed at index 2 = mean(TR_0..TR_2). TR_0=5, TR_1=5, TR_2 = max(6, |108-104|, |102-104|) = 6.
    seed = (Decimal(5) + Decimal(5) + Decimal(6)) / Decimal(n)
    assert result[2] == seed
    # Index 3: TR_3 = max(5, |110-106|, |105-106|) = 5.
    expected = (Decimal(n - 1) * seed + Decimal(5)) / Decimal(n)
    assert result[3] == expected


# ---------------------------------------------------------------------------
# TC_IND_004 — OBV cumulative
# ---------------------------------------------------------------------------


def test_obv_canonical_investopedia_example():
    """REQ_F_IND_001 / REQ_F_IND_002 — OBV cumulative; gain ⇒ +volume,
    flat ⇒ 0, loss ⇒ -volume; no None positions."""
    bars = [
        _bar(h="10", l="10", c="10", v="100"),
        _bar(h="11", l="11", c="11", v="200"),  # +200
        _bar(h="11", l="11", c="11", v="150"),  # flat ⇒ 0
        _bar(h="10", l="10", c="10", v="300"),  # -300
        _bar(h="12", l="12", c="12", v="250"),  # +250
    ]
    result = obv(bars)
    assert len(result) == 5
    assert result[0] == Decimal(0)
    assert result[1] == Decimal(200)
    assert result[2] == Decimal(200)
    assert result[3] == Decimal(-100)
    assert result[4] == Decimal(150)
    # No None elements (REQ_F_IND_002 OBV exception).
    assert all(v is not None for v in result)


# ---------------------------------------------------------------------------
# TC_IND_005 — ADX trending fixture
# ---------------------------------------------------------------------------


def test_adx_returns_none_until_2n_minus_one():
    """REQ_F_IND_002 — ADX first appears at index 2n-1."""
    # 30 trending bars: highs/lows increase 1 per bar.
    bars = [
        _bar(h=str(100 + i), l=str(95 + i), c=str(98 + i)) for i in range(30)
    ]
    result = adx(bars, n=14)
    for i in range(2 * 14 - 1):
        assert result[i] is None, f"index {i} should be None (warmup)"
    # First non-None at index 27.
    assert result[27] is not None


def test_adx_strong_trend_exceeds_25():
    """REQ_F_IND_004 — ADX > 25 confirms trending fixture (canonical
    TA interpretation)."""
    # Strongly trending fixture: each bar +2 on highs/lows.
    bars = [
        _bar(h=str(100 + 2 * i), l=str(95 + 2 * i), c=str(98 + 2 * i))
        for i in range(40)
    ]
    result = adx(bars, n=14)
    last = result[-1]
    assert last is not None
    assert last > Decimal(25)


# ---------------------------------------------------------------------------
# TC_IND_006 — Decimal-only boundary
# ---------------------------------------------------------------------------


def test_sma_rejects_float_input():
    """REQ_F_IND_003 / REQ_SDD_IND_002 — float operand surfaces as
    TypeError at the function boundary."""
    with pytest.raises(TypeError, match="Decimal-only"):
        sma([10.0, 11.0, 12.0], n=2)


def test_rsi_rejects_float_input():
    with pytest.raises(TypeError, match="Decimal-only"):
        rsi([10.0, 11.0], n=14)


# ---------------------------------------------------------------------------
# TC_IND_007 — Determinism within one process
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("indicator", [sma, rsi])
def test_close_indicators_deterministic_within_process(indicator):
    """REQ_F_IND_006 / REQ_SDD_IND_005 — tuple equality across two
    consecutive invocations."""
    closes = [Decimal(100 + i) for i in range(30)]
    if indicator is sma:
        kwargs = {"n": 5}
    else:
        kwargs = {"n": 14}
    a = indicator(closes, **kwargs)
    b = indicator(closes, **kwargs)
    assert a == b


@pytest.mark.parametrize("indicator", [atr, obv, adx])
def test_bar_indicators_deterministic_within_process(indicator):
    bars = [
        _bar(h=str(100 + i), l=str(95 + i), c=str(98 + i)) for i in range(40)
    ]
    if indicator is obv:
        a = indicator(bars)
        b = indicator(bars)
    else:
        a = indicator(bars, n=14)
        b = indicator(bars, n=14)
    assert a == b


# ---------------------------------------------------------------------------
# TC_IND_008 — Determinism across pytest subprocess
# ---------------------------------------------------------------------------


_SUBPROC_SCRIPT = """
import sys
from decimal import Decimal
from trading_system.quant.indicators import sma

closes = [Decimal(100 + i) for i in range(20)]
result = sma(closes, n=5)
# Stringify the tuple deterministically — Decimal repr is stable.
print(",".join("None" if v is None else str(v) for v in result))
"""


def test_indicator_subprocess_determinism():
    """REQ_SDD_IND_005 — two subprocess invocations against the same
    fixture produce identical output. Catches hidden module-level
    state that would survive within one process but not across."""
    r1 = subprocess.run(
        [sys.executable, "-c", _SUBPROC_SCRIPT],
        check=True,
        capture_output=True,
        text=True,
    )
    r2 = subprocess.run(
        [sys.executable, "-c", _SUBPROC_SCRIPT],
        check=True,
        capture_output=True,
        text=True,
    )
    assert r1.stdout == r2.stdout
    # Sanity: the output is non-trivial.
    assert "None" in r1.stdout


# ---------------------------------------------------------------------------
# TC_IND_009 — VolatilityIndexProvider Protocol conformance
# ---------------------------------------------------------------------------


@dataclass
class _StubProvider:
    """MarketDataProvider double — returns a pinned Bar."""

    canned_bar: Bar

    def latest(self, instrument):
        del instrument
        return Ok(self.canned_bar)

    def bars(self, *_a, **_k):
        return Err("data:not_supported")

    def dividends(self, *_a, **_k):
        return Err("data:not_supported")


def test_yfinance_volatility_index_provider_runtime_checkable():
    """REQ_F_IND_005 / REQ_SDD_IND_004 — concrete satisfies the
    Protocol."""
    canned = _bar(h="15", l="14", c="14.5")
    provider = YFinanceVolatilityIndexProvider(provider=_StubProvider(canned_bar=canned))
    assert isinstance(provider, VolatilityIndexProvider)


def test_yfinance_volatility_index_provider_known_symbol_returns_bar():
    """REQ_F_IND_005 — `^VIX` and `^VSTOXX` SHALL pass through to
    the wrapped provider."""
    canned = _bar(h="15", l="14", c="14.5")
    provider = YFinanceVolatilityIndexProvider(provider=_StubProvider(canned_bar=canned))
    result = provider.latest("^VIX")
    assert isinstance(result, Ok)
    assert result.value == canned
    result_eu = provider.latest("^VSTOXX")
    assert isinstance(result_eu, Ok)


def test_yfinance_volatility_index_provider_unknown_symbol_returns_err():
    """REQ_SDD_IND_004 — unknown symbol fails fast with the
    documented categorised Err."""
    provider = YFinanceVolatilityIndexProvider(
        provider=_StubProvider(canned_bar=_bar(h="1", l="1", c="1"))
    )
    result = provider.latest("^FAKE")
    assert isinstance(result, Err)
    assert result.error == "volatility_index:unknown_symbol:^FAKE"


# ---------------------------------------------------------------------------
# TC_IND_010 — Import-graph audit
# ---------------------------------------------------------------------------


def test_indicators_importable_from_any_layer():
    """REQ_NF_IND_001 — the package is runtime-safe. Verified by
    importing it cleanly + asserting the public surface is what
    SDD §13.34 documents."""
    from trading_system.quant import indicators

    for name in ("sma", "rsi", "atr", "obv", "adx"):
        assert hasattr(indicators, name), f"missing public export: {name}"
        assert callable(getattr(indicators, name))
