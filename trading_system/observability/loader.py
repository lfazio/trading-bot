"""YAML loader for ``config/logging.yaml``.

Loads the logging level + format + output stream into a frozen
``LoggingConfig``. Mirrors the existing
``trading_system.safety.loader.load_kill_switch_config`` pattern
(categorised ``Err`` strings; ``yaml.safe_load`` once at the I/O
boundary; runtime mutation forbidden per REQ_SDS_INT_004).

REQ refs: REQ_NF_LOG_001 (timestamped logs), REQ_SDS_CRS_001
(JSON-line schema), REQ_SDS_CFG_001 (configured at startup),
REQ_SDD_API_004 (frozen Config), REQ_SDD_ERR_002 (categorised Errs).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from trading_system.result import Err, Ok, Result, catch


_VALID_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)

OutputFormat = Literal["json", "text"]


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Top-level shape of ``config/logging.yaml``."""

    level: str = "INFO"
    format: OutputFormat = "json"
    # When non-None, logs go to the file at this absolute path
    # (in addition to stderr). v1 wires stderr only — the file sink
    # arrives with the live-deployment hardening sub-CR.
    file_path: str | None = None

    def __post_init__(self) -> None:
        if self.level not in _VALID_LEVELS:
            raise ValueError(
                f"LoggingConfig.level must be one of {sorted(_VALID_LEVELS)}, "
                f"got {self.level!r}"
            )
        if self.format not in ("json", "text"):
            raise ValueError(
                f"LoggingConfig.format must be 'json' or 'text', got {self.format!r}"
            )
        if self.file_path is not None and not self.file_path.strip():
            raise ValueError("LoggingConfig.file_path must be non-empty when set")


_TOP = "logging"


def load_logging_config(path: Path | str) -> Result[LoggingConfig, str]:
    """Parse ``config/logging.yaml`` and return a frozen
    ``LoggingConfig`` or a categorised ``Err`` string.

    Absent file is NOT an error here — the caller (typically
    ``main.py``) may opt into defaults via ``LoggingConfig()`` when
    the path doesn't exist. This loader only fires when the operator
    has supplied a YAML.
    """
    p = Path(path)
    raw_result = catch(lambda: p.read_text(encoding="utf-8"), OSError)
    match raw_result:
        case Err(exc):
            return Err(f"config:io: cannot read {p}: {exc!r}")
        case Ok(text):
            raw_text = text

    parsed_result: Result[Any, BaseException] = catch(
        lambda: yaml.safe_load(raw_text), yaml.YAMLError
    )
    match parsed_result:
        case Err(exc):
            return Err(f"config:parse: invalid YAML at {p}: {exc!r}")
        case Ok(parsed):
            payload = parsed

    # Empty file ⇒ defaults.
    if payload is None:
        return Ok(LoggingConfig())

    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {p} must be a mapping, "
            f"got {type(payload).__name__}"
        )
    section = payload.get(_TOP)
    if section is None:
        # Absent ``logging:`` key ⇒ defaults.
        return Ok(LoggingConfig())
    if not isinstance(section, Mapping):
        return Err(
            f"config:schema: '{_TOP}' section must be a mapping, "
            f"got {type(section).__name__} ({p})"
        )

    kwargs: dict[str, Any] = {}
    if "level" in section:
        lvl = section["level"]
        if not isinstance(lvl, str):
            return Err(
                f"config:schema: logging.level must be a string, "
                f"got {type(lvl).__name__} ({p})"
            )
        kwargs["level"] = lvl.upper()
    if "format" in section:
        fmt = section["format"]
        if not isinstance(fmt, str):
            return Err(
                f"config:schema: logging.format must be a string, "
                f"got {type(fmt).__name__} ({p})"
            )
        kwargs["format"] = fmt
    if "file_path" in section:
        fp = section["file_path"]
        if fp is not None and not isinstance(fp, str):
            return Err(
                f"config:schema: logging.file_path must be a string or null, "
                f"got {type(fp).__name__} ({p})"
            )
        kwargs["file_path"] = fp

    built = catch(lambda: LoggingConfig(**kwargs), ValueError)
    match built:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(cfg):
            return Ok(cfg)
