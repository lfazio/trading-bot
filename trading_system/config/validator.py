"""Centralised startup validator.

The system already ships per-module loaders (``safety/loader.py``,
``risk/loader.py``, ``phase_engine/loader.py``,
``turbo_selector/loader.py``, ``observability/loader.py``, plus
``system.py`` in this package). Each emits categorised ``Err``
strings. The runner here drives every loader against the matching
YAML in one pass and aggregates the failures so the operator sees
**every** bad file in a single cycle rather than fixing them one
restart at a time.

The runner also does a *shape* check for the four YAMLs that don't
yet ship a typed loader (``tax.yaml`` / ``meta_loop.yaml`` /
``structured.yaml``) — verifying they parse + carry the expected
top-level section. Full loaders for those files arrive with the CRs
that need them.

REQ refs: REQ_SDS_CFG_001 (validated at startup), REQ_SDS_CFG_002
(absent file ⇒ defaults), REQ_SDD_ERR_002 (categorised Errs),
REQ_NF_LIF_001 (fail-fast at the lifecycle gate).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from trading_system.accounts.yaml_loader import load_accounts_yaml
from trading_system.config.system import load_system_config
from trading_system.notifications.loader import load_notifications_config
from trading_system.observability.loader import load_logging_config
from trading_system.phase_engine.loader import load_phase_engine
from trading_system.result import Err, Ok, Result, catch
from trading_system.risk.loader import load_risk_config
from trading_system.safety.loader import load_kill_switch_config
from trading_system.turbo_selector.loader import load_turbo_selector_config


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Aggregated outcome of a ``validate_all`` run.

    ``errors`` is the list of categorised ``Err`` strings — one per
    failing file. Empty when every shipped file parsed cleanly.
    ``validated_files`` and ``skipped_files`` together cover the
    full set the runner attempted, so the operator can tell
    "skipped because absent" from "validated and clean".
    """

    errors: tuple[str, ...] = ()
    validated_files: tuple[str, ...] = ()
    skipped_files: tuple[str, ...] = ()

    @property
    def is_ok(self) -> bool:
        return not self.errors


# Each entry: (filename, loader, required) where:
# - filename is relative to the config_dir argument
# - loader takes a Path and returns a Result[Any, str]
# - required=True means the file MUST exist; False means absent ⇒ skip
_TYPED_LOADERS: tuple[tuple[str, Callable[[Path], Result[Any, str]], bool], ...] = (
    ("system.yaml", load_system_config, True),
    ("phases.yaml", load_phase_engine, True),
    ("risk.yaml", load_risk_config, True),
    ("kill_switch.yaml", load_kill_switch_config, True),
    ("turbos.yaml", load_turbo_selector_config, True),
    ("logging.yaml", load_logging_config, False),  # absent ⇒ defaults
    ("accounts.yaml", load_accounts_yaml, False),  # absent ⇒ single-account default
    ("notifications.yaml", load_notifications_config, False),  # absent ⇒ defaults
)


# Files we know parse + have a documented top-level key but no shipped
# typed loader yet. Shape check only.
@dataclass(frozen=True, slots=True)
class _ShapeOnly:
    filename: str
    top_key: str
    required: bool


_SHAPE_ONLY: tuple[_ShapeOnly, ...] = (
    _ShapeOnly("tax.yaml", "tax", required=False),
    _ShapeOnly("meta_loop.yaml", "meta_loop", required=False),
    _ShapeOnly("structured.yaml", "structured", required=False),
    # Phase-A YAMLs whose typed loaders land with their respective
    # Phase-B sub-CRs; the shape check here catches typos in the
    # operator's YAML file (REQ_SDS_CFG_002) ahead of the typed
    # loader landing.
    _ShapeOnly("quant.yaml", "quant", required=False),
    _ShapeOnly("webui.yaml", "webui", required=False),
)


def validate_all(config_dir: Path | str) -> Result[ValidationReport, ValidationReport]:
    """Drive every shipped loader against its YAML.

    Returns ``Ok(ValidationReport)`` when every required file is
    present and parses cleanly. Returns ``Err(ValidationReport)``
    when one or more validations fail — the report's ``errors``
    tuple aggregates every categorised ``Err`` so the operator
    fixes them in one cycle.

    Absent optional files are recorded under ``skipped_files``,
    not ``errors``.
    """
    cd = Path(config_dir)
    if not cd.is_dir():
        report = ValidationReport(
            errors=(f"config:io: config_dir {cd!s} is not a directory",),
        )
        return Err(report)

    errors: list[str] = []
    validated: list[str] = []
    skipped: list[str] = []

    for filename, loader, required in _TYPED_LOADERS:
        path = cd / filename
        if not path.exists():
            if required:
                errors.append(
                    f"config:io: required file {filename} missing in {cd!s}"
                )
            else:
                skipped.append(filename)
            continue
        result = loader(path)
        match result:
            case Err(reason):
                errors.append(reason)
            case Ok(_):
                validated.append(filename)

    for spec in _SHAPE_ONLY:
        path = cd / spec.filename
        if not path.exists():
            if spec.required:
                errors.append(
                    f"config:io: required file {spec.filename} missing in {cd!s}"
                )
            else:
                skipped.append(spec.filename)
            continue
        shape = _check_shape(path, top_key=spec.top_key)
        match shape:
            case Err(reason):
                errors.append(reason)
            case Ok(_):
                validated.append(spec.filename)

    report = ValidationReport(
        errors=tuple(errors),
        validated_files=tuple(sorted(validated)),
        skipped_files=tuple(sorted(skipped)),
    )
    if errors:
        return Err(report)
    return Ok(report)


def _check_shape(path: Path, *, top_key: str) -> Result[None, str]:
    """Minimal validator: YAML parses + top-level mapping carries the
    expected key. Used for files without a typed loader.
    """
    raw_result = catch(lambda: path.read_text(encoding="utf-8"), OSError)
    match raw_result:
        case Err(exc):
            return Err(f"config:io: cannot read {path!s}: {exc!r}")
        case Ok(text):
            raw_text = text
    parsed_result: Result[Any, BaseException] = catch(
        lambda: yaml.safe_load(raw_text), yaml.YAMLError
    )
    match parsed_result:
        case Err(exc):
            return Err(f"config:parse: invalid YAML at {path!s}: {exc!r}")
        case Ok(parsed):
            payload = parsed
    if payload is None:
        return Err(
            f"config:schema: {path!s} is empty (expected '{top_key}' section)"
        )
    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {path!s} must be a mapping, "
            f"got {type(payload).__name__}"
        )
    if top_key not in payload:
        return Err(
            f"config:schema: missing top-level key '{top_key}' in {path!s}"
        )
    return Ok(None)


