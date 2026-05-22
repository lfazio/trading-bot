"""Conformance test for the shared regime fixtures — REQ_TP_FIX_003.

The fixture module ships four functions, one per documented
``MarketRegime`` value. The test asserts the shape + OHLCV
invariants hold for every regime so consumers across screener /
strategies / backtesting / structured products / meta-loop see
consistent, valid bar sequences.
"""

from __future__ import annotations

from trading_system.data.types import Bar
from trading_system.models.phase import MarketRegime

from tests.fixtures.regime import REGIME_FIXTURES


def test_every_market_regime_has_a_fixture() -> None:
    """REQ_TP_FIX_003 — the closed set of ``MarketRegime`` values
    SHALL each have a corresponding fixture function."""
    expected = {r.value for r in MarketRegime}
    assert set(REGIME_FIXTURES.keys()) == expected, (
        f"fixture set {set(REGIME_FIXTURES.keys())} != "
        f"MarketRegime values {expected}"
    )


def test_every_fixture_returns_a_non_empty_bar_tuple() -> None:
    """Every fixture SHALL return a non-empty ``tuple[Bar, ...]``
    on the default day count."""
    for name, fn in REGIME_FIXTURES.items():
        bars = fn()
        assert isinstance(bars, tuple), (
            f"{name} fixture returned {type(bars).__name__}, not tuple"
        )
        assert len(bars) > 0
        assert all(isinstance(b, Bar) for b in bars)


def test_every_fixture_satisfies_ohlc_invariants() -> None:
    """REQ_SDD_DAT_001 family — bars SHALL satisfy
    high ≥ max(open, close) and low ≤ min(open, close)."""
    for name, fn in REGIME_FIXTURES.items():
        bars = fn()
        for bar in bars:
            assert bar.high >= bar.open, (
                f"{name}: bar.high < bar.open at {bar.at}"
            )
            assert bar.high >= bar.close
            assert bar.low <= bar.open
            assert bar.low <= bar.close


def test_bull_fixture_trends_up() -> None:
    """Sanity: BULL fixture's last close SHALL be strictly
    greater than its first open."""
    bars = REGIME_FIXTURES["bull"]()
    assert bars[-1].close > bars[0].open


def test_bear_fixture_trends_down() -> None:
    """Sanity: BEAR fixture's last close SHALL be strictly
    less than its first open."""
    bars = REGIME_FIXTURES["bear"]()
    assert bars[-1].close < bars[0].open


def test_high_vol_fixture_has_larger_swings() -> None:
    """Sanity: HIGH_VOL fixture's per-bar range (high - low) SHALL
    exceed the BULL fixture's average range — this is the
    property a vol-band detector keys on."""
    bull = REGIME_FIXTURES["bull"]()
    hv = REGIME_FIXTURES["high_vol"]()
    avg_range_bull = sum(
        (b.high - b.low) for b in bull
    ) / len(bull)
    avg_range_hv = sum(
        (b.high - b.low) for b in hv
    ) / len(hv)
    assert avg_range_hv > avg_range_bull * 3, (
        f"high_vol avg range {avg_range_hv} not 3× bull {avg_range_bull}"
    )
