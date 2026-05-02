"""Tests for ``trading_system.phase_engine.loader``.

Covers schema validation (REQ_SDS_CFG_002), error categorization
(REQ_SDD_ERR_002 / REQ_SDD_ERR_004), and round-trip from the shipped
``config/phases.yaml`` (REQ_F_CAP_004 / REQ_SDD_CFG_003).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.models.phase import (
    AllocationBucket,
    Phase,
    PhaseConstraints,
)
from trading_system.phase_engine.loader import load_phase_engine
from trading_system.result import Err, Ok

REPO = Path(__file__).resolve().parents[2]
DEFAULT_YAML = REPO / "config" / "phases.yaml"


def write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "phases.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy path: shipped config/phases.yaml
# ---------------------------------------------------------------------------


class TestShippedConfig:
    def test_loads_default_yaml(self) -> None:
        assert DEFAULT_YAML.exists(), DEFAULT_YAML
        result = load_phase_engine(DEFAULT_YAML)
        match result:
            case Ok(engine):
                assert engine.current() == Phase.ONE
                assert engine.bounds == [
                    Decimal("3000"),
                    Decimal("10000"),
                    Decimal("50000"),
                    Decimal("200000"),
                    Decimal("1000000"),
                ]
                assert engine.hysteresis == Decimal("0.10")
                # Sanity-check Phase 1 constraint table.
                pc1 = engine.constraints_for(Phase.ONE)
                assert pc1.max_positions == 3
                assert pc1.max_trades_per_month == 4
                assert pc1.max_drawdown == Decimal("0.15")
                assert pc1.portfolio_vol_cap is None
                assert pc1.allocation_targets[AllocationBucket.STOCK] == Decimal("0.90")
                # Phase 5 / 6 vol cap mandatory (REQ_F_CAP_012).
                assert engine.constraints_for(Phase.FIVE).portfolio_vol_cap == Decimal("0.12")
                assert engine.constraints_for(Phase.SIX).portfolio_vol_cap == Decimal("0.08")
            case Err(reason):
                pytest.fail(f"shipped config/phases.yaml failed to load: {reason}")


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    def test_missing_file(self, tmp_path: Path) -> None:
        match load_phase_engine(tmp_path / "missing.yaml"):
            case Err(reason):
                assert reason.startswith("config:io:")
            case Ok(_):
                pytest.fail("expected Err for missing file")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "phases:\n  bounds: [1, 2, 3\n")
        match load_phase_engine(path):
            case Err(reason):
                assert reason.startswith("config:parse:")
            case Ok(_):
                pytest.fail("expected Err for invalid YAML")

    def test_missing_top_section(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "other: 1\n")
        match load_phase_engine(path):
            case Err(reason):
                assert "missing or non-mapping 'phases'" in reason
            case Ok(_):
                pytest.fail("expected Err for missing section")

    def test_missing_phase_entry(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """\
phases:
  bounds: [3000, 10000, 50000, 200000, 1000000]
  hysteresis: 0.10
  constraints:
    1:
      max_positions: 3
      max_trades_per_month: 4
      allocation_targets: { stock: 1.0 }
      turbo_exposure_max: 0.0
      risk_per_trade_band: [0.01, 0.02]
      max_drawdown: 0.15
""",
        )
        match load_phase_engine(path):
            case Err(reason):
                assert "phases.constraints.2" in reason
            case Ok(_):
                pytest.fail("expected Err for missing phase entry")

    def test_unknown_allocation_bucket(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            _full_yaml_with_phase_1_alloc("unknown: 1.0"),
        )
        match load_phase_engine(path):
            case Err(reason):
                assert "unknown bucket" in reason
            case Ok(_):
                pytest.fail("expected Err for unknown bucket")

    def test_invalid_allocation_sum(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            _full_yaml_with_phase_1_alloc("stock: 0.5, tactical: 0.3"),
        )
        match load_phase_engine(path):
            case Err(reason):
                assert "config:invariant" in reason
                assert "allocation_targets" in reason
            case Ok(_):
                pytest.fail("expected Err for bad allocation sum")

    def test_missing_phase5_vol_cap(self, tmp_path: Path) -> None:
        # Replace Phase 5's portfolio_vol_cap to None and assert the
        # engine-level invariant fires (REQ_F_CAP_012).
        path = write_yaml(tmp_path, _full_yaml(strip_phase5_vol_cap=True))
        match load_phase_engine(path):
            case Err(reason):
                assert "Phase.FIVE" in reason
                assert "portfolio_vol_cap" in reason
            case Ok(_):
                pytest.fail("expected Err for missing Phase 5 vol cap")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_yaml(*, strip_phase5_vol_cap: bool = False) -> str:
    p5 = "0.12" if not strip_phase5_vol_cap else "null"
    return f"""\
phases:
  bounds: [3000, 10000, 50000, 200000, 1000000]
  hysteresis: 0.10
  constraints:
    1:
      max_positions: 3
      max_trades_per_month: 4
      allocation_targets: {{ stock: 0.90, tactical: 0.10 }}
      turbo_exposure_max: 0.0
      risk_per_trade_band: [0.01, 0.02]
      max_drawdown: 0.15
    2:
      max_positions: 6
      max_trades_per_month: 8
      allocation_targets: {{ stock: 0.70, tactical: 0.30 }}
      turbo_exposure_max: 0.05
      risk_per_trade_band: [0.01, 0.02]
      max_drawdown: 0.15
    3:
      max_positions: 12
      max_trades_per_month: 20
      allocation_targets: {{ stock: 0.60, tactical: 0.40 }}
      turbo_exposure_max: 0.15
      risk_per_trade_band: [0.01, 0.02]
      max_drawdown: 0.20
    4:
      max_positions: 20
      max_trades_per_month: 40
      allocation_targets:
        stock: 0.50
        tactical: 0.30
        structured: 0.10
        turbo: 0.20
        cash: -0.10
      turbo_exposure_max: 0.20
      risk_per_trade_band: [0.01, 0.015]
      max_drawdown: 0.20
    5:
      max_positions: 30
      max_trades_per_month: 60
      allocation_targets:
        stock: 0.55
        tactical: 0.15
        structured: 0.15
        turbo: 0.10
        cash: 0.05
      turbo_exposure_max: 0.15
      risk_per_trade_band: [0.005, 0.01]
      max_drawdown: 0.15
      portfolio_vol_cap: {p5}
    6:
      max_positions: 50
      max_trades_per_month: 100
      allocation_targets:
        stock: 0.60
        tactical: 0.15
        structured: 0.10
        turbo: 0.10
        cash: 0.05
      turbo_exposure_max: 0.10
      risk_per_trade_band: [0.0025, 0.0075]
      max_drawdown: 0.12
      portfolio_vol_cap: 0.08
"""


def _full_yaml_with_phase_1_alloc(alloc_body: str) -> str:
    base = _full_yaml()
    target = "allocation_targets: { stock: 0.90, tactical: 0.10 }"
    replacement = f"allocation_targets: {{ {alloc_body} }}"
    return base.replace(target, replacement, 1)


def _placeholder_use(_pc: PhaseConstraints) -> None:
    # Keeps PhaseConstraints in the test imports' resolution graph
    # for type-checker purposes; pytest never calls this.
    return None
