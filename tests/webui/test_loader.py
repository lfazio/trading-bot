"""``config/webui.yaml`` loader tests — CR-004 Phase B
(REQ_SDD_WEB_008).
"""

from __future__ import annotations

from pathlib import Path

from trading_system.result import Err, Ok
from trading_system.webui.loader import WebUIConfig, load_webui_config


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "webui.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_when_section_missing(tmp_path: Path) -> None:
    """Empty file + missing ``webui:`` key ⇒ defaults so the no-
    config path keeps working in single-deployment setups."""
    path = _write(tmp_path, "other_top: {}\n")
    match load_webui_config(path):
        case Ok(cfg):
            assert cfg == WebUIConfig()
        case _:
            raise AssertionError("expected Ok(defaults)")


def test_empty_file_returns_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    match load_webui_config(path):
        case Ok(cfg):
            assert cfg == WebUIConfig()
        case _:
            raise AssertionError("expected Ok(defaults)")


def test_present_section_overrides_fields(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "webui:\n"
        "  host: 0.0.0.0\n"
        "  port: 9090\n"
        "  idempotency_backend: persistence\n"
        "  idempotency_ttl_seconds: 1200\n"
        "  job_workers: 4\n",
    )
    match load_webui_config(path):
        case Ok(cfg):
            assert cfg.host == "0.0.0.0"
            assert cfg.port == 9090
            assert cfg.idempotency_backend == "persistence"
            assert cfg.idempotency_ttl_seconds == 1200
            assert cfg.job_workers == 4
        case _:
            raise AssertionError("expected Ok(...)")


# ---------------------------------------------------------------------------
# Categorised Errs
# ---------------------------------------------------------------------------


def test_missing_file_returns_io_err(tmp_path: Path) -> None:
    match load_webui_config(tmp_path / "absent.yaml"):
        case Err(reason):
            assert reason.startswith("config:io:")
        case _:
            raise AssertionError("expected Err(config:io)")


def test_malformed_yaml_returns_parse_err(tmp_path: Path) -> None:
    path = _write(tmp_path, "webui: { unclosed: [\n")
    match load_webui_config(path):
        case Err(reason):
            assert reason.startswith("config:parse:")
        case _:
            raise AssertionError("expected Err(config:parse)")


def test_top_level_not_mapping_returns_schema_err(tmp_path: Path) -> None:
    path = _write(tmp_path, "- one\n- two\n")
    match load_webui_config(path):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err(config:schema)")


def test_section_not_mapping_returns_schema_err(tmp_path: Path) -> None:
    path = _write(tmp_path, "webui: [list, not, mapping]\n")
    match load_webui_config(path):
        case Err(reason):
            assert reason.startswith("config:schema:") and "webui" in reason
        case _:
            raise AssertionError("expected Err(config:schema)")


def test_port_wrong_type(tmp_path: Path) -> None:
    path = _write(tmp_path, "webui:\n  port: \"8080\"\n")
    match load_webui_config(path):
        case Err(reason):
            assert reason.startswith("config:schema:") and "port" in reason
        case _:
            raise AssertionError("expected Err(config:schema)")


def test_unknown_backend_returns_invariant_err(tmp_path: Path) -> None:
    path = _write(tmp_path, "webui:\n  idempotency_backend: redis\n")
    match load_webui_config(path):
        case Err(reason):
            assert reason.startswith("config:invariant:")
        case _:
            raise AssertionError("expected Err(config:invariant)")


def test_port_out_of_range_returns_invariant_err(tmp_path: Path) -> None:
    path = _write(tmp_path, "webui:\n  port: 70000\n")
    match load_webui_config(path):
        case Err(reason):
            assert reason.startswith("config:invariant:")
        case _:
            raise AssertionError("expected Err(config:invariant)")


def test_zero_job_workers_returns_invariant_err(tmp_path: Path) -> None:
    path = _write(tmp_path, "webui:\n  job_workers: 0\n")
    match load_webui_config(path):
        case Err(reason):
            assert reason.startswith("config:invariant:")
        case _:
            raise AssertionError("expected Err(config:invariant)")


# ---------------------------------------------------------------------------
# Frozen invariant
# ---------------------------------------------------------------------------


def test_webui_config_is_frozen() -> None:
    cfg = WebUIConfig()
    import pytest

    with pytest.raises((AttributeError, TypeError)):
        cfg.port = 9090  # type: ignore[misc]
