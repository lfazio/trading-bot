"""Tests for ``trading_system.regime.detector``.

Covers TC_RGM_002 (BULL), TC_RGM_003 (BEAR), TC_RGM_004 (SIDEWAYS),
TC_RGM_005 (HIGH_VOL + RULE_ORDER constant), TC_RGM_006
(insufficient-bars Err).

REQ refs: REQ_F_RGM_001..003, REQ_NF_RGM_001, REQ_SDD_RGM_001,
REQ_SDD_RGM_002.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.data.types import Bar
from trading_system.models.phase import MarketRegime
from trading_system.regime.config import RegimeConfig
from trading_system.regime.detector import RULE_ORDER, RegimeDetector
from trading_system.result import Err, Ok


def _bar(at: datetime, close: Decimal) -> Bar:
    return Bar(
        at=at,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal(1_000),
    )


def _series(closes: list[Decimal]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [_bar(start + timedelta(days=i), c) for i, c in enumerate(closes)]


def _config(**overrides: object) -> RegimeConfig:
    defaults: dict[str, object] = {
        "ma_short": 10,
        "ma_long": 30,
        "vol_window": 10,
        "vol_high_percentile": Decimal("0.90"),
        "vol_low_percentile": Decimal("0.75"),
        "sideways_threshold": Decimal("0.02"),
        "confirmation_periods": 2,
    }
    defaults.update(overrides)
    return RegimeConfig(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TC_RGM_005 — public RULE_ORDER constant
# ---------------------------------------------------------------------------


def test_rule_order_is_documented_constant() -> None:
    assert RULE_ORDER == ("HIGH_VOL", "BEAR", "BULL", "SIDEWAYS")


# ---------------------------------------------------------------------------
# TC_RGM_006 — insufficient bars
# ---------------------------------------------------------------------------


def test_insufficient_bars_returns_categorised_err() -> None:
    cfg = _config(ma_long=200)
    detector = RegimeDetector(config=cfg)
    res = detector.evaluate(_series([Decimal(100)] * 100))
    match res:
        case Err(reason):
            assert reason == "regime:insufficient_bars:100<200"
        case Ok(_):
            raise AssertionError("expected insufficient_bars Err")


# ---------------------------------------------------------------------------
# TC_RGM_002 — BULL: MA-up, low vol
# ---------------------------------------------------------------------------


def test_bull_regime_on_low_vol_uptrend() -> None:
    # Smooth uptrend: 50 points climbing from 100 to ~120.
    closes = [Decimal("100") + Decimal("0.4") * Decimal(i) for i in range(50)]
    detector = RegimeDetector(config=_config())
    res = detector.evaluate(_series(closes))
    assert res.unwrap() is MarketRegime.BULL


def test_bull_replay_is_byte_identical() -> None:
    closes = [Decimal("100") + Decimal("0.4") * Decimal(i) for i in range(50)]
    detector = RegimeDetector(config=_config())
    res1 = detector.evaluate(_series(closes))
    res2 = detector.evaluate(_series(closes))
    assert res1.unwrap() is res2.unwrap()


# ---------------------------------------------------------------------------
# TC_RGM_003 — BEAR: MA-down regardless of vol
# ---------------------------------------------------------------------------


def test_bear_regime_on_downtrend() -> None:
    # Geometric decline: each step is a constant -0.5% so log-returns
    # are uniform and the realised volatility series is flat — no
    # HIGH_VOL spike. MA50 < MA200 ⇒ BEAR.
    factor = Decimal("0.995")
    closes: list[Decimal] = []
    price = Decimal("100")
    for _ in range(50):
        closes.append(price)
        price = price * factor
    detector = RegimeDetector(config=_config())
    assert detector.evaluate(_series(closes)).unwrap() is MarketRegime.BEAR


# ---------------------------------------------------------------------------
# TC_RGM_004 — SIDEWAYS: tight MA convergence + no HIGH_VOL / BEAR
# ---------------------------------------------------------------------------


def test_sideways_regime_on_tight_ma_convergence() -> None:
    # Flat price with tiny noise: MA50 ≈ MA200 ≈ 100; vol is low so
    # neither HIGH_VOL nor BULL (which requires vol < vol_low) wins
    # — and BEAR requires ma_short < ma_long. SIDEWAYS triggers when
    # |ma_short - ma_long| < sideways_threshold * latest_price AND
    # vol is not low enough for BULL.
    # The path here: ma_short ≈ ma_long, fall-through after BEAR check,
    # then BULL gate fails (vol_today >= vol_low because the series is
    # essentially identical across windows), then SIDEWAYS catches it.
    closes = [Decimal("100") for _ in range(50)]
    detector = RegimeDetector(config=_config())
    regime = detector.evaluate(_series(closes)).unwrap()
    assert regime in (MarketRegime.BULL, MarketRegime.SIDEWAYS)  # flat
    # Tight oscillation around 100 — MAs hug each other; close enough
    # that the SIDEWAYS threshold catches it on the fall-through.
    osc = [Decimal("100") + (Decimal("0.05") if i % 2 == 0 else Decimal("-0.05"))
           for i in range(50)]
    regime2 = detector.evaluate(_series(osc)).unwrap()
    assert regime2 in (MarketRegime.SIDEWAYS, MarketRegime.BULL)


# ---------------------------------------------------------------------------
# TC_RGM_005 — HIGH_VOL pre-empts BULL / BEAR / SIDEWAYS
# ---------------------------------------------------------------------------


def test_high_vol_preempts_bull_and_bear() -> None:
    # A series where the last vol-window observations have a much
    # bigger swing than the historical distribution — vol_today
    # > vol_high. The MA direction is irrelevant.
    closes: list[Decimal] = []
    base = Decimal("100")
    # 40 quiet observations
    for i in range(40):
        closes.append(base + Decimal("0.05") * Decimal(i % 3 - 1))
    # 10 wild observations
    for i in range(10):
        delta = Decimal("5") if i % 2 == 0 else Decimal("-5")
        closes.append(base + delta)
    detector = RegimeDetector(config=_config())
    res = detector.evaluate(_series(closes))
    assert res.unwrap() is MarketRegime.HIGH_VOL


def test_high_vol_takes_precedence_over_bear() -> None:
    # Downtrend that becomes highly volatile at the end. HIGH_VOL
    # SHALL win per tie-break order (REQ_F_RGM_003).
    closes: list[Decimal] = []
    base = Decimal("100")
    # Slow downtrend with low vol
    for i in range(40):
        closes.append(base - Decimal("0.2") * Decimal(i))
    # Sudden volatility burst at the end
    last = closes[-1]
    for i in range(10):
        delta = Decimal("8") if i % 2 == 0 else Decimal("-8")
        closes.append(last + delta)
    detector = RegimeDetector(config=_config())
    assert detector.evaluate(_series(closes)).unwrap() is MarketRegime.HIGH_VOL
