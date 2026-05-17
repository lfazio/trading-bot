"""Tests for ``trading_system.strategy_lab.quant.loader``
(REQ_SDD_QNT_008)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.result import Err, Ok
from trading_system.strategy_lab.quant.loader import (
    OverfittingConfig,
    QuantConfig,
    ValidatorConfig,
    load_quant_config,
)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "quant.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# OverfittingConfig invariants
# ---------------------------------------------------------------------------


def test_overfitting_defaults() -> None:
    o = OverfittingConfig()
    assert o.ratio_max == Decimal("0.10")
    assert o.ic_floor == Decimal("0.30")


def test_overfitting_rejects_zero_ratio_max() -> None:
    with pytest.raises(ValueError, match="ratio_max"):
        OverfittingConfig(ratio_max=Decimal("0"))


def test_overfitting_rejects_ratio_above_one() -> None:
    with pytest.raises(ValueError, match="ratio_max"):
        OverfittingConfig(ratio_max=Decimal("1.5"))


def test_overfitting_rejects_ic_floor_out_of_range() -> None:
    with pytest.raises(ValueError, match="ic_floor"):
        OverfittingConfig(ic_floor=Decimal("1.1"))
    with pytest.raises(ValueError, match="ic_floor"):
        OverfittingConfig(ic_floor=Decimal("-1.1"))


# ---------------------------------------------------------------------------
# QuantConfig defaults
# ---------------------------------------------------------------------------


def test_quant_config_defaults() -> None:
    c = QuantConfig()
    assert c.validator == ValidatorConfig()
    assert c.overfitting == OverfittingConfig()


# ---------------------------------------------------------------------------
# Loader happy path
# ---------------------------------------------------------------------------


def test_loads_explicit_fields(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
quant:
  validator:
    bounds_table:
      sharpe: {lo: -2, hi: 2}
      adjusted_sharpe: {lo: -2.5, hi: 2.5}
    metric_vocabulary:
      - sharpe
      - adjusted_sharpe
    min_duration_days_for_1d: 60
    min_window_for_intraday_days: 2
  overfitting:
    ratio_max: "0.05"
    ic_floor: "0.50"
""",
    )
    cfg = load_quant_config(p).unwrap()
    assert cfg.validator.bounds_table["sharpe"] == (
        Decimal("-2"),
        Decimal("2"),
    )
    assert cfg.validator.bounds_table["adjusted_sharpe"] == (
        Decimal("-2.5"),
        Decimal("2.5"),
    )
    assert cfg.validator.metric_vocabulary == frozenset(
        {"sharpe", "adjusted_sharpe"}
    )
    assert cfg.validator.min_duration_days_for_1d == 60
    assert cfg.validator.min_window_for_intraday_days == 2
    assert cfg.overfitting.ratio_max == Decimal("0.05")
    assert cfg.overfitting.ic_floor == Decimal("0.50")


def test_empty_file_returns_defaults(tmp_path: Path) -> None:
    p = _write(tmp_path, "")
    assert load_quant_config(p).unwrap() == QuantConfig()


def test_absent_section_returns_defaults(tmp_path: Path) -> None:
    p = _write(tmp_path, "other_section: 1\n")
    assert load_quant_config(p).unwrap() == QuantConfig()


def test_partial_section_returns_partial_overrides(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
quant:
  overfitting:
    ratio_max: "0.05"
""",
    )
    cfg = load_quant_config(p).unwrap()
    assert cfg.overfitting.ratio_max == Decimal("0.05")
    # Sibling field keeps its default.
    assert cfg.overfitting.ic_floor == Decimal("0.30")
    # Validator stays at defaults.
    assert cfg.validator == ValidatorConfig()


# ---------------------------------------------------------------------------
# Loader error categories
# ---------------------------------------------------------------------------


def test_missing_file_returns_io_err(tmp_path: Path) -> None:
    match load_quant_config(tmp_path / "ghost.yaml"):
        case Err(reason):
            assert reason.startswith("config:io:")
        case _:
            raise AssertionError("expected Err")


def test_malformed_yaml_returns_parse_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "quant: {a: b\n")
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:parse:")
        case _:
            raise AssertionError("expected Err")


def test_non_mapping_top_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "- one\n- two\n")
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_non_mapping_validator_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "quant:\n  validator: not-a-mapping\n")
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:") and "validator" in reason
        case _:
            raise AssertionError("expected Err")


def test_bounds_table_lo_above_hi_returns_invariant_err(
    tmp_path: Path,
) -> None:
    p = _write(
        tmp_path,
        "quant:\n  validator:\n    bounds_table:\n      sharpe: {lo: 5, hi: 1}\n",
    )
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:invariant:") and "lo" in reason
        case _:
            raise AssertionError("expected Err")


def test_bounds_table_missing_lo_returns_schema_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "quant:\n  validator:\n    bounds_table:\n      sharpe: {hi: 1}\n",
    )
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:") and "lo" in reason.lower()
        case _:
            raise AssertionError("expected Err")


def test_unparseable_bound_value_returns_schema_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "quant:\n  validator:\n    bounds_table:\n      sharpe: {lo: 'not-a-number', hi: 3}\n",
    )
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_empty_metric_vocabulary_returns_invariant_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "quant:\n  validator:\n    metric_vocabulary: []\n",
    )
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:invariant:")
        case _:
            raise AssertionError("expected Err")


def test_non_int_min_duration_returns_schema_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "quant:\n  validator:\n    min_duration_days_for_1d: 'thirty'\n",
    )
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_zero_min_duration_returns_schema_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "quant:\n  validator:\n    min_duration_days_for_1d: 0\n",
    )
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_bad_overfitting_ratio_max_returns_schema_err(
    tmp_path: Path,
) -> None:
    p = _write(
        tmp_path,
        "quant:\n  overfitting:\n    ratio_max: not-a-number\n",
    )
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_overfitting_ratio_zero_returns_invariant_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "quant:\n  overfitting:\n    ratio_max: '0'\n",
    )
    match load_quant_config(p):
        case Err(reason):
            assert reason.startswith("config:invariant:")
        case _:
            raise AssertionError("expected Err")
