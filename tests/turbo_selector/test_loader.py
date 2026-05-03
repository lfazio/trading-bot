"""Tests for ``trading_system.turbo_selector.loader``.

Mirrors the phase-engine loader tests: the shipped YAML round-trips
cleanly, malformed inputs map to categorized ``Err`` strings, and
schema invariants surface as ``config:invariant``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.result import Err, Ok
from trading_system.turbo_selector.loader import load_turbo_selector_config

REPO = Path(__file__).resolve().parents[2]
DEFAULT_YAML = REPO / "config" / "turbos.yaml"


def write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "turbos.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _full_yaml(*, threshold: str = "0.50") -> str:
    return f"""\
turbos:
  filter:
    knockout_min_distance: 0.05
    spread_max: 0.015
    min_liquidity: 100000
    max_volatility: 0.50
  scoring:
    weights: [0.35, 0.25, 0.20, 0.20]
    threshold: {threshold}
"""


# ---------------------------------------------------------------------------
# Happy path — shipped config/turbos.yaml
# ---------------------------------------------------------------------------


class TestShippedConfig:
    def test_loads_default_yaml(self) -> None:
        assert DEFAULT_YAML.exists(), DEFAULT_YAML
        result = load_turbo_selector_config(DEFAULT_YAML)
        match result:
            case Ok(cfg):
                assert cfg.knockout_min_distance == Decimal("0.05")
                assert cfg.spread_max == Decimal("0.015")
                assert cfg.threshold == Decimal("0.50")
                assert cfg.weights == (
                    Decimal("0.35"),
                    Decimal("0.25"),
                    Decimal("0.20"),
                    Decimal("0.20"),
                )
            case Err(reason):
                pytest.fail(f"shipped config/turbos.yaml failed to load: {reason}")


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    def test_missing_file(self, tmp_path: Path) -> None:
        match load_turbo_selector_config(tmp_path / "nope.yaml"):
            case Err(reason):
                assert reason.startswith("config:io:")
            case Ok(_):
                pytest.fail("expected Err")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "turbos: [1, 2,\n")
        match load_turbo_selector_config(path):
            case Err(reason):
                assert reason.startswith("config:parse:")
            case Ok(_):
                pytest.fail("expected Err")

    def test_missing_top_section(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "other: 1\n")
        match load_turbo_selector_config(path):
            case Err(reason):
                assert "missing or non-mapping 'turbos'" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_missing_filter_key(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
turbos:
  filter:
    knockout_min_distance: 0.05
    spread_max: 0.015
    min_liquidity: 100000
  scoring:
    weights: [0.35, 0.25, 0.20, 0.20]
    threshold: 0.50
""",
        )
        match load_turbo_selector_config(path):
            case Err(reason):
                assert "max_volatility" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_weights_wrong_length(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
turbos:
  filter:
    knockout_min_distance: 0.05
    spread_max: 0.015
    min_liquidity: 100000
    max_volatility: 0.50
  scoring:
    weights: [0.5, 0.5]
    threshold: 0.50
""",
        )
        match load_turbo_selector_config(path):
            case Err(reason):
                assert "4-element" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_weights_bad_sum(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
turbos:
  filter:
    knockout_min_distance: 0.05
    spread_max: 0.015
    min_liquidity: 100000
    max_volatility: 0.50
  scoring:
    weights: [0.40, 0.20, 0.20, 0.10]
    threshold: 0.50
""",
        )
        match load_turbo_selector_config(path):
            case Err(reason):
                assert "config:invariant" in reason
                assert "weights" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_invariant_failure_threshold_above_one(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, _full_yaml(threshold="1.5"))
        match load_turbo_selector_config(path):
            case Err(reason):
                assert "config:invariant" in reason
                assert "threshold" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_non_numeric_weight(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
turbos:
  filter:
    knockout_min_distance: 0.05
    spread_max: 0.015
    min_liquidity: 100000
    max_volatility: 0.50
  scoring:
    weights: [0.35, 0.25, "abc", 0.20]
    threshold: 0.50
""",
        )
        match load_turbo_selector_config(path):
            case Err(reason):
                assert "weights[2]" in reason
            case Ok(_):
                pytest.fail("expected Err")
