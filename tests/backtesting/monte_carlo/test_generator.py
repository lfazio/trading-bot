"""TC_MCS_003 (seed derivation), TC_MCS_004 (block bootstrap shape),
TC_MCS_005 (GBM degenerate), TC_MCS_006 (regime-stitched composition).

REQ refs: REQ_F_MCS_002, REQ_SDS_MCS_002 / REQ_SDS_MCS_004,
REQ_SDD_MCS_003.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.backtesting.monte_carlo import (
    BlockBootstrapGenerator,
    GBMGenerator,
    GBMParams,
    RegimeStitchedGenerator,
    seed_for_path,
)
from trading_system.data.types import Bar
from trading_system.result import Err, Ok


_START = datetime(2024, 1, 2, tzinfo=UTC)


def _bars(closes: list[str]) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            at=_START + timedelta(days=i),
            open=Decimal(c),
            high=Decimal(c),
            low=Decimal(c),
            close=Decimal(c),
            volume=Decimal("1"),
        )
        for i, c in enumerate(closes)
    )


# ---------------------------------------------------------------------------
# TC_MCS_003 — seed_for_path derivation formula
# ---------------------------------------------------------------------------


def test_seed_for_path_is_deterministic() -> None:
    """Two callers with the same ``(seed, path_index)`` see the same
    derived seed. REQ_SDS_MCS_002."""
    assert seed_for_path(42, 0) == seed_for_path(42, 0)
    assert seed_for_path(42, 1) == seed_for_path(42, 1)
    # And the two indices differ from each other.
    assert seed_for_path(42, 0) != seed_for_path(42, 1)


def test_seed_for_path_pinned_values() -> None:
    """Pinned values verify the SHA-256-big-endian-first-8-bytes formula.
    Mutating the formula (big → little endian, different slice) breaks
    these constants — by design (TC_MCS_003)."""
    assert seed_for_path(42, 0) == 13789621580203367573
    assert seed_for_path(42, 1) == 362040032084776300


def test_seed_for_path_seed_changes_propagate() -> None:
    assert seed_for_path(41, 0) != seed_for_path(42, 0)
    assert seed_for_path(43, 0) != seed_for_path(42, 0)


# ---------------------------------------------------------------------------
# TC_MCS_004 — BlockBootstrap shape + determinism
# ---------------------------------------------------------------------------


def test_block_bootstrap_returns_n_steps_bars() -> None:
    bars = _bars([f"{100 + i}" for i in range(60)])  # 60 historical
    gen = BlockBootstrapGenerator(block_length=5)
    res = gen.generate(bars, seed=1, n_steps=30)
    match res:
        case Ok(path):
            # Anchor (index 0) + 29 path returns = 30 bars.
            assert len(path) == 30
        case Err(reason):
            raise AssertionError(reason)


def test_block_bootstrap_anchors_at_historical_close() -> None:
    bars = _bars(["100", "101", "102", "103", "104", "105", "106", "107"])
    gen = BlockBootstrapGenerator(block_length=3)
    path = gen.generate(bars, seed=99, n_steps=20).unwrap()
    assert path[0].close == bars[0].close  # anchor preserved


def test_block_bootstrap_deterministic() -> None:
    bars = _bars([f"{100 + i}" for i in range(60)])
    gen = BlockBootstrapGenerator(block_length=10)
    a = gen.generate(bars, seed=7, n_steps=40).unwrap()
    b = gen.generate(bars, seed=7, n_steps=40).unwrap()
    assert tuple(p.close for p in a) == tuple(p.close for p in b)


def test_block_bootstrap_different_seed_differs() -> None:
    bars = _bars([f"{100 + i}" for i in range(60)])
    gen = BlockBootstrapGenerator(block_length=10)
    a = gen.generate(bars, seed=1, n_steps=40).unwrap()
    b = gen.generate(bars, seed=2, n_steps=40).unwrap()
    assert tuple(p.close for p in a) != tuple(p.close for p in b)


def test_block_bootstrap_empty_history_returns_err() -> None:
    gen = BlockBootstrapGenerator(block_length=5)
    match gen.generate((), seed=1, n_steps=10):
        case Err(reason):
            assert reason.category == "mc:empty_history"
        case _:
            raise AssertionError("expected Err on empty history")


# ---------------------------------------------------------------------------
# TC_MCS_005 — GBM degenerate sigma=0 + determinism
# ---------------------------------------------------------------------------


def test_gbm_zero_sigma_zero_mu_constant_close() -> None:
    bars = _bars(["100"] * 20)
    gen = GBMGenerator(gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0")))
    path = gen.generate(bars, seed=1, n_steps=15).unwrap()
    # With mu=0 + sigma=0, every return is zero ⇒ close stays at the
    # historical anchor.
    for bar in path:
        assert bar.close == bars[0].close


def test_gbm_with_sigma_is_deterministic_given_seed() -> None:
    bars = _bars(["100"] * 20)
    gen = GBMGenerator(gbm_params=GBMParams(mu=Decimal("0.001"), sigma=Decimal("0.01")))
    a = gen.generate(bars, seed=42, n_steps=15).unwrap()
    b = gen.generate(bars, seed=42, n_steps=15).unwrap()
    assert tuple(p.close for p in a) == tuple(p.close for p in b)


def test_gbm_different_seeds_diverge() -> None:
    bars = _bars(["100"] * 20)
    gen = GBMGenerator(gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.05")))
    a = gen.generate(bars, seed=1, n_steps=15).unwrap()
    b = gen.generate(bars, seed=2, n_steps=15).unwrap()
    assert tuple(p.close for p in a) != tuple(p.close for p in b)


# ---------------------------------------------------------------------------
# TC_MCS_006 — RegimeStitched composition + determinism
# ---------------------------------------------------------------------------


def test_regime_stitched_without_detector_falls_back_to_default_label() -> None:
    """When ``detector is None`` every bar gets the ``"default"`` label
    and the generator behaves like a single-regime bootstrap."""
    bars = _bars([f"{100 + i}" for i in range(60)])
    gen = RegimeStitchedGenerator(regime_window=10, detector=None)
    a = gen.generate(bars, seed=5, n_steps=30).unwrap()
    b = gen.generate(bars, seed=5, n_steps=30).unwrap()
    assert tuple(p.close for p in a) == tuple(p.close for p in b)


def test_regime_stitched_with_detector_runs_labels() -> None:
    """A stub detector returns a constant regime so labelling is
    exercised; determinism is preserved."""

    class _StubRegime:
        value = "BULL"

    class _StubDetector:
        def evaluate(self, bars: tuple[Bar, ...]) -> _StubRegime:
            return _StubRegime()

    bars = _bars([f"{100 + i}" for i in range(60)])
    gen = RegimeStitchedGenerator(regime_window=10, detector=_StubDetector())
    a = gen.generate(bars, seed=5, n_steps=30).unwrap()
    b = gen.generate(bars, seed=5, n_steps=30).unwrap()
    assert tuple(p.close for p in a) == tuple(p.close for p in b)


def test_regime_stitched_rejects_zero_window() -> None:
    # The MCConfig validator would normally catch this; the generator
    # also rejects defensively so direct callers get the same Err.
    bars = _bars(["100"] * 20)
    gen = RegimeStitchedGenerator(regime_window=0, detector=None)
    match gen.generate(bars, seed=1, n_steps=10):
        case Err(reason):
            assert reason.category == "mc:config_mismatch:regime_window"
        case _:
            raise AssertionError("expected Err on regime_window=0")
