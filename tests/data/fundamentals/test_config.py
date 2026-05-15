"""Tests for ``trading_system.data.fundamentals.config``.

Covers TC_FND_001 (FundamentalsConfig invariants + documented defaults).

REQ refs: REQ_F_FND_003, REQ_NF_FND_001.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_system.data.fundamentals.config import FundamentalsConfig


def test_default_constants_match_srs_defaults() -> None:
    cfg = FundamentalsConfig()
    assert cfg.csv_path == Path("data/seed_fundamentals.csv")
    assert cfg.max_age_days == 547


def test_max_age_days_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="max_age_days"):
        FundamentalsConfig(max_age_days=0)
    with pytest.raises(ValueError, match="max_age_days"):
        FundamentalsConfig(max_age_days=-1)


def test_config_is_frozen() -> None:
    cfg = FundamentalsConfig()
    with pytest.raises(Exception):
        cfg.max_age_days = 100  # type: ignore[misc]
