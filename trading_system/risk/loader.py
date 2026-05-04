"""YAML loader bridging ``config/risk.yaml`` -> ``RiskConfig``.

Mirrors the phase / turbo loaders. Errors are categorized
``Result`` strings per REQ_SDD_ERR_002.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from trading_system.models.instrument import InstrumentClass
from trading_system.models.phase import MarketRegime
from trading_system.result import Err, Ok, Result, catch
from trading_system.risk.config import RiskConfig

_TOP = "risk"
_REQUIRED = ("single_asset_cap", "correlation_max", "correlation_window_days")


def load_risk_config(path: Path | str) -> Result[RiskConfig, str]:
    """Load and validate ``config/risk.yaml``."""
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

    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {p} must be a mapping, got {type(payload).__name__}"
        )
    section = payload.get(_TOP)
    if not isinstance(section, Mapping):
        return Err(f"config:schema: missing or non-mapping '{_TOP}' section ({p})")

    return _build(section, source=str(p))


def _build(  # noqa: PLR0911, PLR0912 - linear validator chain
    section: Mapping[str, Any], *, source: str
) -> Result[RiskConfig, str]:
    for key in _REQUIRED:
        if key not in section:
            return Err(f"config:schema: risk.{key} missing ({source})")

    sac_result = _decimal(section["single_asset_cap"], key="risk.single_asset_cap", source=source)
    match sac_result:
        case Err(reason):
            return Err(reason)
        case Ok(single_asset_cap):
            pass

    corr_result = _decimal(section["correlation_max"], key="risk.correlation_max", source=source)
    match corr_result:
        case Err(reason):
            return Err(reason)
        case Ok(correlation_max):
            pass

    window_raw = section["correlation_window_days"]
    if not isinstance(window_raw, int) or isinstance(window_raw, bool):
        return Err(
            f"config:schema: risk.correlation_window_days must be int "
            f"(got {type(window_raw).__name__}) ({source})"
        )

    forbidden_raw = section.get("forbidden_regimes_for", {})
    if not isinstance(forbidden_raw, Mapping):
        return Err(f"config:schema: risk.forbidden_regimes_for must be a mapping ({source})")
    forbidden: dict[InstrumentClass, tuple[MarketRegime, ...]] = {}
    for cls_raw, regimes_raw in forbidden_raw.items():
        try:
            cls = InstrumentClass(str(cls_raw))
        except ValueError:
            return Err(
                f"config:schema: risk.forbidden_regimes_for unknown class '{cls_raw}' ({source})"
            )
        if not isinstance(regimes_raw, list):
            return Err(
                f"config:schema: risk.forbidden_regimes_for[{cls_raw}] must be a list ({source})"
            )
        regimes: list[MarketRegime] = []
        for r_raw in regimes_raw:
            try:
                regimes.append(MarketRegime(str(r_raw)))
            except ValueError:
                return Err(
                    f"config:schema: risk.forbidden_regimes_for[{cls_raw}] "
                    f"unknown regime '{r_raw}' ({source})"
                )
        forbidden[cls] = tuple(regimes)

    cfg_result = catch(
        lambda: RiskConfig(
            single_asset_cap=single_asset_cap,
            correlation_max=correlation_max,
            correlation_window_days=window_raw,
            forbidden_regimes_for=forbidden,
        ),
        ValueError,
    )
    match cfg_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({source})")
        case Ok(cfg):
            return Ok(cfg)


def _decimal(value: Any, *, key: str, source: str) -> Result[Decimal, str]:
    if isinstance(value, Decimal):
        return Ok(value)
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        return Err(f"config:schema: {key} must be numeric, got bool ({source})")
    if isinstance(value, int | float | str):
        try:
            return Ok(Decimal(str(value)))
        except (InvalidOperation, ValueError):
            return Err(
                f"config:schema: {key} could not be parsed as Decimal (value={value!r}) ({source})"
            )
    return Err(
        f"config:schema: {key} must be numeric or a string, got {type(value).__name__} ({source})"
    )
