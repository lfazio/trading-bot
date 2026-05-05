"""Tests for ``trading_system.safety.loader``."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.result import Err, Ok
from trading_system.safety.loader import (
    ExecutionTriggerConfig,
    FinancialTriggerConfig,
    load_kill_switch_config,
)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_YAML = REPO / "config" / "kill_switch.yaml"


def write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "kill_switch.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Trigger config dataclasses
# ---------------------------------------------------------------------------


class TestFinancialTriggerConfig:
    def test_defaults(self) -> None:
        cfg = FinancialTriggerConfig()
        assert cfg.single_day_loss == Decimal("0.05")
        assert cfg.rapid_decline_pct == Decimal("0.10")
        assert cfg.rapid_decline_days == 5

    @pytest.mark.parametrize(
        "kwargs, msg",
        [
            ({"single_day_loss": Decimal(0)}, "single_day_loss"),
            ({"single_day_loss": Decimal("1.5")}, "single_day_loss"),
            ({"rapid_decline_pct": Decimal(0)}, "rapid_decline_pct"),
            ({"rapid_decline_pct": Decimal("1.5")}, "rapid_decline_pct"),
            ({"rapid_decline_days": 0}, "rapid_decline_days"),
            ({"rapid_decline_days": -1}, "rapid_decline_days"),
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, object], msg: str) -> None:
        with pytest.raises(ValueError, match=msg):
            FinancialTriggerConfig(**kwargs)


class TestExecutionTriggerConfig:
    def test_defaults(self) -> None:
        cfg = ExecutionTriggerConfig()
        assert cfg.rejection_threshold == Decimal("0.20")
        assert cfg.slippage_anomaly_sigma == Decimal("3.0")

    @pytest.mark.parametrize(
        "kwargs, msg",
        [
            ({"rejection_threshold": Decimal(0)}, "rejection_threshold"),
            ({"rejection_threshold": Decimal("1.5")}, "rejection_threshold"),
            ({"slippage_anomaly_sigma": Decimal(0)}, "slippage_anomaly_sigma"),
            ({"slippage_anomaly_sigma": Decimal("-1")}, "slippage_anomaly_sigma"),
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, object], msg: str) -> None:
        with pytest.raises(ValueError, match=msg):
            ExecutionTriggerConfig(**kwargs)


# ---------------------------------------------------------------------------
# Loader — happy path
# ---------------------------------------------------------------------------


class TestShippedConfig:
    def test_loads_default_yaml(self) -> None:
        assert DEFAULT_YAML.exists(), DEFAULT_YAML
        result = load_kill_switch_config(DEFAULT_YAML)
        match result:
            case Ok(cfg):
                assert cfg.financial.single_day_loss == Decimal("0.05")
                assert cfg.financial.rapid_decline_pct == Decimal("0.10")
                assert cfg.financial.rapid_decline_days == 5
                assert cfg.execution.rejection_threshold == Decimal("0.20")
                assert cfg.require_manual_recovery is True
            case Err(reason):
                pytest.fail(f"shipped config/kill_switch.yaml failed to load: {reason}")


# ---------------------------------------------------------------------------
# Loader — error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    def test_missing_file(self, tmp_path: Path) -> None:
        match load_kill_switch_config(tmp_path / "nope.yaml"):
            case Err(reason):
                assert reason.startswith("config:io:")
            case Ok(_):
                pytest.fail("expected Err")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "kill_switch: [1, 2,\n")
        match load_kill_switch_config(path):
            case Err(reason):
                assert reason.startswith("config:parse:")
            case Ok(_):
                pytest.fail("expected Err")

    def test_missing_top_section(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "other: 1\n")
        match load_kill_switch_config(path):
            case Err(reason):
                assert "missing or non-mapping 'kill_switch'" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_invariant_failure(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
kill_switch:
  financial:
    single_day_loss: 1.5
""",
        )
        match load_kill_switch_config(path):
            case Err(reason):
                assert "config:invariant" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_rapid_decline_days_must_be_int(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
kill_switch:
  financial:
    rapid_decline:
      pct: 0.10
      days: "five"
""",
        )
        match load_kill_switch_config(path):
            case Err(reason):
                assert "rapid_decline.days" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_require_manual_token_must_be_bool(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
kill_switch:
  recovery:
    require_manual_token: "yes"
""",
        )
        match load_kill_switch_config(path):
            case Err(reason):
                assert "require_manual_token" in reason
            case Ok(_):
                pytest.fail("expected Err")

    def test_partial_overrides_keep_defaults(self, tmp_path: Path) -> None:
        # Override only single_day_loss; the other fields keep defaults.
        path = write_yaml(
            tmp_path,
            """\
kill_switch:
  financial:
    single_day_loss: 0.03
""",
        )
        match load_kill_switch_config(path):
            case Ok(cfg):
                assert cfg.financial.single_day_loss == Decimal("0.03")
                assert cfg.financial.rapid_decline_pct == Decimal("0.10")
                assert cfg.execution.rejection_threshold == Decimal("0.20")
            case Err(reason):
                pytest.fail(f"expected Ok, got Err: {reason}")
