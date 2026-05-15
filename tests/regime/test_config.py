"""Tests for ``trading_system.regime.config``.

Covers TC_RGM_001 (RegimeConfig invariants + documented defaults).

REQ refs: REQ_F_RGM_002, REQ_F_RGM_006, REQ_SDD_RGM_001.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.regime.config import RegimeConfig


def test_default_constants_match_srs_defaults() -> None:
    cfg = RegimeConfig()
    assert cfg.ma_short == 50
    assert cfg.ma_long == 200
    assert cfg.vol_window == 60
    assert cfg.vol_high_percentile == Decimal("0.90")
    assert cfg.vol_low_percentile == Decimal("0.75")
    assert cfg.sideways_threshold == Decimal("0.02")
    assert cfg.confirmation_periods == 2
    assert cfg.bar_source == "synthetic_eu"


def test_ma_short_must_be_less_than_ma_long() -> None:
    with pytest.raises(ValueError, match="ma_short"):
        RegimeConfig(ma_short=200, ma_long=200)
    with pytest.raises(ValueError, match="ma_short"):
        RegimeConfig(ma_short=250, ma_long=200)


def test_vol_percentile_ordering_invariant() -> None:
    with pytest.raises(ValueError, match="vol_low_percentile"):
        RegimeConfig(
            vol_low_percentile=Decimal("0.95"),
            vol_high_percentile=Decimal("0.90"),
        )


def test_vol_percentiles_must_be_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="vol_low_percentile"):
        RegimeConfig(vol_low_percentile=Decimal("-0.01"))
    with pytest.raises(ValueError, match="vol_high_percentile"):
        RegimeConfig(vol_high_percentile=Decimal("1.5"))


def test_sideways_threshold_must_be_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="sideways_threshold"):
        RegimeConfig(sideways_threshold=Decimal("1.5"))


def test_confirmation_periods_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="confirmation_periods"):
        RegimeConfig(confirmation_periods=0)
    with pytest.raises(ValueError, match="confirmation_periods"):
        RegimeConfig(confirmation_periods=-1)


def test_ma_windows_must_be_positive() -> None:
    with pytest.raises(ValueError, match="ma_short"):
        RegimeConfig(ma_short=0, ma_long=100)
    with pytest.raises(ValueError, match="ma_long"):
        RegimeConfig(ma_short=10, ma_long=0)


def test_vol_window_must_be_greater_than_one() -> None:
    with pytest.raises(ValueError, match="vol_window"):
        RegimeConfig(vol_window=1)


def test_bar_source_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="bar_source"):
        RegimeConfig(bar_source="")
    with pytest.raises(ValueError, match="bar_source"):
        RegimeConfig(bar_source="   ")


def test_config_is_frozen() -> None:
    cfg = RegimeConfig()
    with pytest.raises(Exception):
        cfg.ma_short = 100  # type: ignore[misc]
