"""TC_MCS_001 — ``MCConfig.__post_init__`` invariants.

REQ refs: REQ_F_MCS_003, REQ_SDD_MCS_005.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.backtesting.monte_carlo import GBMParams, MCConfig, RNGSeed


# ---------------------------------------------------------------------------
# TC_MCS_001 — bounds + generator/field cross-checks
# ---------------------------------------------------------------------------


def test_n_paths_below_floor_rejected() -> None:
    with pytest.raises(ValueError, match="mc:n_paths_out_of_bounds"):
        MCConfig(
            generator="gbm",
            n_paths=99,
            seed=RNGSeed(1),
            gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.01")),
        )


def test_n_paths_above_ceiling_rejected() -> None:
    with pytest.raises(ValueError, match="mc:n_paths_out_of_bounds"):
        MCConfig(
            generator="gbm",
            n_paths=100_001,
            seed=RNGSeed(1),
            gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.01")),
        )


def test_block_bootstrap_without_block_length_rejected() -> None:
    with pytest.raises(ValueError, match="mc:config_mismatch:block_length"):
        MCConfig(generator="block_bootstrap", n_paths=100, seed=RNGSeed(1))


def test_gbm_without_params_rejected() -> None:
    with pytest.raises(ValueError, match="mc:config_mismatch:gbm_params"):
        MCConfig(generator="gbm", n_paths=100, seed=RNGSeed(1))


def test_regime_stitched_without_window_rejected() -> None:
    with pytest.raises(ValueError, match="mc:config_mismatch:regime_window"):
        MCConfig(generator="regime_stitched", n_paths=100, seed=RNGSeed(1))


def test_block_length_must_be_positive() -> None:
    with pytest.raises(ValueError, match="mc:bad_block_length"):
        MCConfig(
            generator="block_bootstrap",
            n_paths=100,
            seed=RNGSeed(1),
            block_length=0,
        )


def test_gbm_params_sigma_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="mc:config_mismatch:gbm_params"):
        GBMParams(mu=Decimal("0"), sigma=Decimal("-0.01"))


def test_gbm_params_zero_sigma_allowed() -> None:
    """Zero sigma ⇒ deterministic path = pure ``mu`` drift. Valid v1
    degenerate case (TC_MCS_005)."""
    params = GBMParams(mu=Decimal("0.001"), sigma=Decimal("0"))
    assert params.sigma == Decimal("0")


def test_valid_block_bootstrap_constructs() -> None:
    cfg = MCConfig(
        generator="block_bootstrap",
        n_paths=200,
        seed=RNGSeed(42),
        block_length=20,
    )
    assert cfg.block_length == 20
    assert cfg.n_paths == 200


def test_valid_gbm_constructs() -> None:
    cfg = MCConfig(
        generator="gbm",
        n_paths=500,
        seed=RNGSeed(7),
        gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.02")),
    )
    assert cfg.gbm_params is not None
    assert cfg.gbm_params.sigma == Decimal("0.02")


def test_valid_regime_stitched_constructs() -> None:
    cfg = MCConfig(
        generator="regime_stitched",
        n_paths=300,
        seed=RNGSeed(11),
        regime_window=60,
    )
    assert cfg.regime_window == 60
