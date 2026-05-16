"""Tests for ``trading_system.config.validator``.

Covers happy-path validation (every shipped YAML clean), per-file
error categories, error aggregation across multiple bad files,
missing-required vs absent-optional handling, and the
``ValidationReport`` shape.

REQ refs: REQ_SDS_CFG_001, REQ_SDS_CFG_002, REQ_SDD_ERR_002.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from trading_system.config import ValidationReport, validate_all
from trading_system.result import Err, Ok


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_REAL_CONFIG = _REPO_ROOT / "config"


# ---------------------------------------------------------------------------
# Happy path against the shipped config/
# ---------------------------------------------------------------------------


def test_shipped_config_dir_is_valid() -> None:
    """The real ``config/`` directory SHALL pass startup validation.
    This is the canary that catches a developer adding a bad value
    while doing day-to-day work."""
    result = validate_all(_REAL_CONFIG)
    match result:
        case Ok(report):
            assert report.is_ok
            assert "system.yaml" in report.validated_files
            assert "phases.yaml" in report.validated_files
            assert "risk.yaml" in report.validated_files
            assert "kill_switch.yaml" in report.validated_files
            assert "turbos.yaml" in report.validated_files
            # logging.yaml is optional; ships now so it should be validated.
            assert "logging.yaml" in report.validated_files
            # Shape-only validators on tax / meta_loop / structured.
            assert "tax.yaml" in report.validated_files
            assert "meta_loop.yaml" in report.validated_files
            assert "structured.yaml" in report.validated_files
        case Err(report):
            raise AssertionError(
                f"shipped config failed validation: {report.errors}"
            )


# ---------------------------------------------------------------------------
# Single-file failures surface as categorised Errs
# ---------------------------------------------------------------------------


def _copy_config(tmp_path: Path) -> Path:
    """Copy the shipped ``config/`` to a tmp dir so individual tests
    can mutate files without disturbing siblings."""
    dest = tmp_path / "config"
    shutil.copytree(_REAL_CONFIG, dest)
    return dest


def test_corrupt_system_yaml_surfaces_category_err(tmp_path: Path) -> None:
    dest = _copy_config(tmp_path)
    (dest / "system.yaml").write_text("system:\n  starting_capital: not-a-mapping\n")
    result = validate_all(dest)
    match result:
        case Err(report):
            assert any(
                "config:schema:" in e and "starting_capital" in e
                for e in report.errors
            )
        case _:
            raise AssertionError("expected Err")


def test_corrupt_risk_yaml_surfaces_category_err(tmp_path: Path) -> None:
    dest = _copy_config(tmp_path)
    (dest / "risk.yaml").write_text(": malformed yaml :::")
    result = validate_all(dest)
    match result:
        case Err(report):
            assert any(e.startswith("config:parse:") for e in report.errors)
        case _:
            raise AssertionError("expected Err")


def test_corrupt_phases_yaml_surfaces_category_err(tmp_path: Path) -> None:
    dest = _copy_config(tmp_path)
    (dest / "phases.yaml").write_text("phases:\n  bounds: not-a-list\n")
    result = validate_all(dest)
    match result:
        case Err(report):
            assert any(e.startswith("config:schema:") for e in report.errors)
        case _:
            raise AssertionError("expected Err")


def test_corrupt_logging_yaml_surfaces_category_err(tmp_path: Path) -> None:
    dest = _copy_config(tmp_path)
    (dest / "logging.yaml").write_text("logging:\n  level: VERBOSE\n")
    result = validate_all(dest)
    match result:
        case Err(report):
            assert any(
                e.startswith("config:invariant:") and "level" in e
                for e in report.errors
            )
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# Error aggregation — multiple bad files in one pass
# ---------------------------------------------------------------------------


def test_multiple_failures_aggregate_in_one_report(tmp_path: Path) -> None:
    """The operator SHALL see every bad file in one cycle — no
    fix-one-restart-loop."""
    dest = _copy_config(tmp_path)
    (dest / "risk.yaml").write_text(": malformed :::")
    (dest / "logging.yaml").write_text("logging:\n  level: VERBOSE\n")
    (dest / "system.yaml").write_text("system:\n  starting_capital: 5\n")
    result = validate_all(dest)
    match result:
        case Err(report):
            assert len(report.errors) >= 3
            assert any("risk.yaml" in e or "config:parse:" in e for e in report.errors)
            assert any("level" in e for e in report.errors)
            assert any("starting_capital" in e for e in report.errors)
        case _:
            raise AssertionError("expected Err with multiple errors")


# ---------------------------------------------------------------------------
# Missing-required vs absent-optional
# ---------------------------------------------------------------------------


def test_missing_required_file_surfaces_io_err(tmp_path: Path) -> None:
    dest = _copy_config(tmp_path)
    (dest / "phases.yaml").unlink()
    result = validate_all(dest)
    match result:
        case Err(report):
            assert any(
                "config:io:" in e and "phases.yaml" in e and "missing" in e
                for e in report.errors
            )
        case _:
            raise AssertionError("expected Err")


def test_absent_optional_file_is_skipped(tmp_path: Path) -> None:
    dest = _copy_config(tmp_path)
    (dest / "logging.yaml").unlink()
    result = validate_all(dest)
    match result:
        case Ok(report):
            assert "logging.yaml" in report.skipped_files
            assert "logging.yaml" not in report.validated_files
        case Err(report):
            raise AssertionError(
                f"absent logging.yaml SHALL be skipped, got errors: {report.errors}"
            )


def test_absent_optional_shape_check_is_skipped(tmp_path: Path) -> None:
    dest = _copy_config(tmp_path)
    (dest / "tax.yaml").unlink()
    result = validate_all(dest)
    match result:
        case Ok(report):
            assert "tax.yaml" in report.skipped_files
        case _:
            raise AssertionError("expected Ok")


# ---------------------------------------------------------------------------
# Bad config_dir
# ---------------------------------------------------------------------------


def test_non_directory_config_dir_returns_err(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("hi")
    result = validate_all(not_a_dir)
    match result:
        case Err(report):
            assert any("config:io:" in e for e in report.errors)
        case _:
            raise AssertionError("expected Err")


def test_nonexistent_config_dir_returns_err(tmp_path: Path) -> None:
    result = validate_all(tmp_path / "ghost")
    match result:
        case Err(report):
            assert any("config:io:" in e for e in report.errors)
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# ValidationReport invariants
# ---------------------------------------------------------------------------


def test_report_is_frozen() -> None:
    r = ValidationReport()
    with pytest.raises(Exception):
        r.errors = ("x",)  # type: ignore[misc]


def test_report_is_ok_property() -> None:
    assert ValidationReport().is_ok
    assert not ValidationReport(errors=("boom",)).is_ok


def test_report_validated_files_sorted() -> None:
    """Deterministic ordering of the report fields supports
    byte-identical replays under REQ_NF_DET_001."""
    result = validate_all(_REAL_CONFIG).unwrap()
    assert list(result.validated_files) == sorted(result.validated_files)
    assert list(result.skipped_files) == sorted(result.skipped_files)


# ---------------------------------------------------------------------------
# Shape-only validator catches obvious file-name typos
# ---------------------------------------------------------------------------


def test_shape_only_validator_rejects_missing_top_key(tmp_path: Path) -> None:
    dest = _copy_config(tmp_path)
    (dest / "tax.yaml").write_text("wrong_top_key:\n  rate: 0.30\n")
    result = validate_all(dest)
    match result:
        case Err(report):
            assert any(
                "config:schema:" in e and "'tax'" in e and "tax.yaml" in e
                for e in report.errors
            )
        case _:
            raise AssertionError("expected Err")


def test_shape_only_validator_rejects_empty_file(tmp_path: Path) -> None:
    dest = _copy_config(tmp_path)
    (dest / "tax.yaml").write_text("")
    result = validate_all(dest)
    match result:
        case Err(report):
            assert any(
                "config:schema:" in e and "tax.yaml" in e and "empty" in e
                for e in report.errors
            )
        case _:
            raise AssertionError("expected Err")
