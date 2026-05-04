"""Tests for ``trading_system.risk.loader``."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.models.instrument import InstrumentClass
from trading_system.models.phase import MarketRegime
from trading_system.result import Err, Ok
from trading_system.risk.loader import load_risk_config

REPO = Path(__file__).resolve().parents[2]
DEFAULT_YAML = REPO / "config" / "risk.yaml"


def write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "risk.yaml"
    p.write_text(body, encoding="utf-8")
    return p


class TestShippedConfig:
    def test_loads_default_yaml(self) -> None:
        assert DEFAULT_YAML.exists(), DEFAULT_YAML
        result = load_risk_config(DEFAULT_YAML)
        match result:
            case Ok(cfg):
                assert cfg.single_asset_cap == Decimal("0.30")
                assert cfg.correlation_max == Decimal("0.85")
                assert cfg.correlation_window_days == 60
                assert cfg.regimes_forbidden_for(InstrumentClass.STRUCTURED) == (
                    MarketRegime.HIGH_VOL,
                    MarketRegime.BEAR,
                )
                assert cfg.regimes_forbidden_for(InstrumentClass.TURBO) == (MarketRegime.HIGH_VOL,)
            case Err(reason):
                pytest.fail(f"shipped config/risk.yaml failed to load: {reason}")


class TestErrorPaths:
    def test_missing_file(self, tmp_path: Path) -> None:
        match load_risk_config(tmp_path / "nope.yaml"):
            case Err(reason):
                assert reason.startswith("config:io:")
            case Ok(_):
                pytest.fail("expected Err")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "risk: [1,\n")
        match load_risk_config(path):
            case Err(reason):
                assert reason.startswith("config:parse:")
            case Ok(_):
                pytest.fail("expected Err")

    def test_missing_top_section(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "other: 1\n")
        match load_risk_config(path):
            case Err(reason):
                assert "missing or non-mapping 'risk'" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_missing_required_key(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
risk:
  correlation_max: 0.85
  correlation_window_days: 60
""",
        )
        match load_risk_config(path):
            case Err(reason):
                assert "single_asset_cap" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_unknown_class_in_forbidden(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
risk:
  single_asset_cap: 0.30
  correlation_max: 0.85
  correlation_window_days: 60
  forbidden_regimes_for:
    unknown_cls: ["high_vol"]
""",
        )
        match load_risk_config(path):
            case Err(reason):
                assert "unknown class" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_unknown_regime(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
risk:
  single_asset_cap: 0.30
  correlation_max: 0.85
  correlation_window_days: 60
  forbidden_regimes_for:
    structured: ["panic"]
""",
        )
        match load_risk_config(path):
            case Err(reason):
                assert "unknown regime" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_invariant_failure(self, tmp_path: Path) -> None:
        # correlation_max > 1 -> RiskConfig invariant.
        path = write_yaml(
            tmp_path,
            """\
risk:
  single_asset_cap: 0.30
  correlation_max: 1.5
  correlation_window_days: 60
""",
        )
        match load_risk_config(path):
            case Err(reason):
                assert "config:invariant" in reason
            case Ok(_):
                pytest.fail("expected Err")
