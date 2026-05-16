"""Tests for ``trading_system.observability.loader``.

Covers happy-path YAML parsing, defaults when absent, and the
categorised ``Err`` shape for every failure mode (REQ_SDD_ERR_002).
"""

from __future__ import annotations

from pathlib import Path

from trading_system.observability import LoggingConfig, load_logging_config
from trading_system.result import Err, Ok


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "logging.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# LoggingConfig invariants
# ---------------------------------------------------------------------------


def test_defaults() -> None:
    cfg = LoggingConfig()
    assert cfg.level == "INFO"
    assert cfg.format == "json"
    assert cfg.file_path is None


def test_rejects_unknown_level() -> None:
    import pytest

    with pytest.raises(ValueError, match="level"):
        LoggingConfig(level="VERBOSE")  # type: ignore[arg-type]


def test_rejects_bad_format() -> None:
    import pytest

    with pytest.raises(ValueError, match="format"):
        LoggingConfig(format="csv")  # type: ignore[arg-type]


def test_rejects_blank_file_path() -> None:
    import pytest

    with pytest.raises(ValueError, match="file_path"):
        LoggingConfig(file_path="   ")


# ---------------------------------------------------------------------------
# Loader happy path
# ---------------------------------------------------------------------------


def test_loads_explicit_fields(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
logging:
  level: DEBUG
  format: text
  file_path: /var/log/trading.jsonl
""",
    )
    res = load_logging_config(p)
    match res:
        case Ok(cfg):
            assert cfg.level == "DEBUG"
            assert cfg.format == "text"
            assert cfg.file_path == "/var/log/trading.jsonl"
        case Err(reason):
            raise AssertionError(reason)


def test_uppercases_level(tmp_path: Path) -> None:
    p = _write(tmp_path, "logging:\n  level: info\n")
    cfg = load_logging_config(p).unwrap()
    assert cfg.level == "INFO"


def test_absent_section_returns_defaults(tmp_path: Path) -> None:
    p = _write(tmp_path, "some_other_section: 1\n")
    cfg = load_logging_config(p).unwrap()
    assert cfg == LoggingConfig()


def test_empty_file_returns_defaults(tmp_path: Path) -> None:
    p = _write(tmp_path, "")
    cfg = load_logging_config(p).unwrap()
    assert cfg == LoggingConfig()


def test_null_file_path_is_default(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
logging:
  level: INFO
  format: json
  file_path: null
""",
    )
    cfg = load_logging_config(p).unwrap()
    assert cfg.file_path is None


# ---------------------------------------------------------------------------
# Loader error categories
# ---------------------------------------------------------------------------


def test_missing_file_returns_io_err(tmp_path: Path) -> None:
    res = load_logging_config(tmp_path / "nonexistent.yaml")
    match res:
        case Err(reason):
            assert reason.startswith("config:io:")
        case _:
            raise AssertionError("expected Err")


def test_malformed_yaml_returns_parse_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "logging: {level: DEBUG\n")  # missing close brace
    match load_logging_config(p):
        case Err(reason):
            assert reason.startswith("config:parse:")
        case _:
            raise AssertionError("expected Err")


def test_non_mapping_top_level_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "- one\n- two\n")
    match load_logging_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_invalid_level_returns_invariant_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "logging:\n  level: VERBOSE\n")
    match load_logging_config(p):
        case Err(reason):
            assert reason.startswith("config:invariant:")
        case _:
            raise AssertionError("expected Err")


def test_non_string_level_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "logging:\n  level: 5\n")
    match load_logging_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_non_string_format_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "logging:\n  format: 3\n")
    match load_logging_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_non_string_file_path_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "logging:\n  file_path: 5\n")
    match load_logging_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_non_mapping_logging_section_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "logging:\n  - one\n  - two\n")
    match load_logging_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")
