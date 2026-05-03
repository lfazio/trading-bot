"""YAML loader bridging ``config/turbos.yaml`` -> ``TurboSelectorConfig``.

Mirrors the phase-engine loader pattern: I/O at the boundary,
categorized ``Result`` errors, no top-level engine reads of files
(REQ_SDD_IMP_006). Errors carry the ``config:`` prefix per
REQ_SDD_ERR_002.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from trading_system.result import Err, Ok, Result, catch
from trading_system.turbo_selector.config import TurboSelectorConfig


class TurboSelectorLoadError(ValueError):
    """Internal exception used by ``load_turbo_selector_config``'s
    ``catch`` boundary; callers see ``Result[Err]`` instead."""


_TOP = "turbos"
_FILTER_KEYS = (
    "knockout_min_distance",
    "spread_max",
    "min_liquidity",
    "max_volatility",
)
_SCORING_KEYS = ("weights", "threshold")
_WEIGHT_COUNT = 4


def load_turbo_selector_config(path: Path | str) -> Result[TurboSelectorConfig, str]:
    """Load and validate ``config/turbos.yaml`` into a
    ``TurboSelectorConfig`` instance."""
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
) -> Result[TurboSelectorConfig, str]:
    filter_section = section.get("filter")
    if not isinstance(filter_section, Mapping):
        return Err(f"config:schema: turbos.filter must be a mapping ({source})")
    for key in _FILTER_KEYS:
        if key not in filter_section:
            return Err(f"config:schema: turbos.filter missing key '{key}' ({source})")

    scoring_section = section.get("scoring")
    if not isinstance(scoring_section, Mapping):
        return Err(f"config:schema: turbos.scoring must be a mapping ({source})")
    for key in _SCORING_KEYS:
        if key not in scoring_section:
            return Err(f"config:schema: turbos.scoring missing key '{key}' ({source})")

    filter_decimals: dict[str, Decimal] = {}
    for key in _FILTER_KEYS:
        d_result = _decimal(filter_section[key], key=f"turbos.filter.{key}", source=source)
        match d_result:
            case Err(reason):
                return Err(reason)
            case Ok(value):
                filter_decimals[key] = value

    weights_raw = scoring_section["weights"]
    if not isinstance(weights_raw, list) or len(weights_raw) != _WEIGHT_COUNT:
        return Err(f"config:schema: turbos.scoring.weights must be a 4-element list ({source})")
    weights_result = _decimals(weights_raw, key="turbos.scoring.weights", source=source)
    match weights_result:
        case Err(reason):
            return Err(reason)
        case Ok(weights_list):
            weights = (
                weights_list[0],
                weights_list[1],
                weights_list[2],
                weights_list[3],
            )

    threshold_result = _decimal(
        scoring_section["threshold"], key="turbos.scoring.threshold", source=source
    )
    match threshold_result:
        case Err(reason):
            return Err(reason)
        case Ok(threshold):
            pass

    cfg_result = catch(
        lambda: TurboSelectorConfig(
            knockout_min_distance=filter_decimals["knockout_min_distance"],
            spread_max=filter_decimals["spread_max"],
            min_liquidity=filter_decimals["min_liquidity"],
            max_volatility=filter_decimals["max_volatility"],
            weights=weights,
            threshold=threshold,
        ),
        ValueError,
    )
    match cfg_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({source})")
        case Ok(cfg):
            return Ok(cfg)


def _decimals(values: list[Any], *, key: str, source: str) -> Result[list[Decimal], str]:
    out: list[Decimal] = []
    for i, v in enumerate(values):
        sub = _decimal(v, key=f"{key}[{i}]", source=source)
        match sub:
            case Err(reason):
                return Err(reason)
            case Ok(d):
                out.append(d)
    return Ok(out)


def _decimal(value: Any, *, key: str, source: str) -> Result[Decimal, str]:
    if isinstance(value, Decimal):
        return Ok(value)
    if isinstance(value, bool):  # bool is an int subclass; reject
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
