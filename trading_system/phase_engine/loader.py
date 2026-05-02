"""YAML loader bridging ``config/phases.yaml`` to ``PhaseEngine``.

This is the only place that knows the YAML schema; downstream modules
receive a fully-typed ``PhaseEngine`` instance and never re-read the
file (REQ_SDS_INT_004 — frozen Config; REQ_SDD_API_004).

REQ refs:
- REQ_F_CAP_004 — phase boundaries from configuration.
- REQ_SDS_CFG_002 — schema validation runs at startup; failures are
  fail-fast (mapped to ``Err`` here so the caller can decide whether
  to fail-fast or surface diagnostics).
- REQ_SDD_ERR_002 — categorized error strings; the loader uses
  ``config:`` as its prefix.
- REQ_SDD_IMP_006 — engine modules contain no top-level I/O; this
  loader is the I/O boundary, layered above the engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from trading_system.models.phase import (
    AllocationBucket,
    Phase,
    PhaseConstraints,
)
from trading_system.phase_engine.engine import PhaseEngine
from trading_system.result import Err, Ok, Result, catch


class PhaseEngineLoadError(ValueError):
    """Raised by ``load_phase_engine`` when the YAML cannot be parsed
    into a ``PhaseEngine``. Use the categorized ``Result`` API instead
    of raising in callers — this exception is internal to the loader's
    panic-style error path and is captured by ``catch``.
    """


_REQUIRED_TOP_KEY = "phases"
_REQUIRED_PHASE_KEYS = (
    "max_positions",
    "max_trades_per_month",
    "allocation_targets",
    "turbo_exposure_max",
    "risk_per_trade_band",
    "max_drawdown",
)


def load_phase_engine(
    path: Path | str,
    *,
    initial_phase: Phase = Phase.ONE,
) -> Result[PhaseEngine, str]:
    """Load and validate ``config/phases.yaml`` into a ``PhaseEngine``.

    Returns ``Err("config:<reason>")`` on any malformed input; the
    caller decides whether to fail-fast (REQ_SDS_CFG_002 says yes for
    runtime startup, but the loader is policy-free — it just reports).
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

    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {p} must be a mapping, got {type(payload).__name__}"
        )
    section = payload.get(_REQUIRED_TOP_KEY)
    if not isinstance(section, Mapping):
        return Err(f"config:schema: missing or non-mapping '{_REQUIRED_TOP_KEY}' section")

    return _build(section, initial_phase=initial_phase, source=str(p))


def _build(  # noqa: PLR0911, PLR0912 - linear validator chain; splitting hurts clarity
    section: Mapping[str, Any], *, initial_phase: Phase, source: str
) -> Result[PhaseEngine, str]:
    bounds_raw = section.get("bounds")
    if not isinstance(bounds_raw, list):
        return Err(f"config:schema: phases.bounds must be a list ({source})")
    bounds_result = _decimals(bounds_raw, key="phases.bounds", source=source)
    match bounds_result:
        case Err(reason):
            return Err(reason)
        case Ok(bounds):
            pass

    hysteresis_raw = section.get("hysteresis", "0.10")
    hysteresis_result = _decimal(hysteresis_raw, key="phases.hysteresis", source=source)
    match hysteresis_result:
        case Err(reason):
            return Err(reason)
        case Ok(hysteresis):
            pass

    constraints_raw = section.get("constraints")
    if not isinstance(constraints_raw, Mapping):
        return Err(f"config:schema: phases.constraints must be a mapping ({source})")

    constraints: dict[Phase, PhaseConstraints] = {}
    for phase in Phase:
        entry = constraints_raw.get(phase.value)
        if entry is None:
            entry = constraints_raw.get(str(phase.value))
        if not isinstance(entry, Mapping):
            return Err(
                f"config:schema: phases.constraints.{phase.value} must be a mapping ({source})"
            )
        built = _build_constraints(entry, phase=phase, source=source)
        match built:
            case Err(reason):
                return Err(reason)
            case Ok(pc):
                constraints[phase] = pc

    engine_result = catch(
        lambda: PhaseEngine(
            bounds=bounds,
            hysteresis=hysteresis,
            constraints=constraints,
            initial_phase=initial_phase,
        ),
        ValueError,
    )
    match engine_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({source})")
        case Ok(engine):
            return Ok(engine)


_RISK_BAND_LEN = 2


def _build_constraints(  # noqa: PLR0911, PLR0912 - linear validator chain; splitting hurts clarity
    entry: Mapping[str, Any], *, phase: Phase, source: str
) -> Result[PhaseConstraints, str]:
    for key in _REQUIRED_PHASE_KEYS:
        if key not in entry:
            return Err(
                f"config:schema: phases.constraints.{phase.value} missing key '{key}' ({source})"
            )

    alloc_raw = entry["allocation_targets"]
    if not isinstance(alloc_raw, Mapping):
        return Err(
            f"config:schema: phases.constraints.{phase.value}.allocation_targets "
            f"must be a mapping ({source})"
        )
    alloc: dict[AllocationBucket, Decimal] = {}
    for raw_key, raw_value in alloc_raw.items():
        try:
            bucket = AllocationBucket(str(raw_key))
        except ValueError:
            return Err(
                f"config:schema: phases.constraints.{phase.value}.allocation_targets "
                f"unknown bucket '{raw_key}' ({source})"
            )
        v_result = _decimal(
            raw_value,
            key=f"phases.constraints.{phase.value}.allocation_targets.{raw_key}",
            source=source,
        )
        match v_result:
            case Err(reason):
                return Err(reason)
            case Ok(value):
                alloc[bucket] = value

    band_raw = entry["risk_per_trade_band"]
    if not isinstance(band_raw, list) or len(band_raw) != _RISK_BAND_LEN:
        return Err(
            f"config:schema: phases.constraints.{phase.value}.risk_per_trade_band "
            f"must be [lo, hi] ({source})"
        )
    band_decimals = _decimals(
        band_raw,
        key=f"phases.constraints.{phase.value}.risk_per_trade_band",
        source=source,
    )
    match band_decimals:
        case Err(reason):
            return Err(reason)
        case Ok(values):
            band = (values[0], values[1])

    turbo_max_result = _decimal(
        entry["turbo_exposure_max"],
        key=f"phases.constraints.{phase.value}.turbo_exposure_max",
        source=source,
    )
    match turbo_max_result:
        case Err(reason):
            return Err(reason)
        case Ok(turbo_max):
            pass

    max_dd_result = _decimal(
        entry["max_drawdown"],
        key=f"phases.constraints.{phase.value}.max_drawdown",
        source=source,
    )
    match max_dd_result:
        case Err(reason):
            return Err(reason)
        case Ok(max_dd):
            pass

    vol_cap_raw = entry.get("portfolio_vol_cap")
    vol_cap: Decimal | None
    if vol_cap_raw is None:
        vol_cap = None
    else:
        vol_cap_result = _decimal(
            vol_cap_raw,
            key=f"phases.constraints.{phase.value}.portfolio_vol_cap",
            source=source,
        )
        match vol_cap_result:
            case Err(reason):
                return Err(reason)
            case Ok(value):
                vol_cap = value

    pc_result = catch(
        lambda: PhaseConstraints(
            max_positions=int(entry["max_positions"]),
            max_trades_per_month=int(entry["max_trades_per_month"]),
            allocation_targets=alloc,
            turbo_exposure_max=turbo_max,
            risk_per_trade_band=band,
            max_drawdown=max_dd,
            portfolio_vol_cap=vol_cap,
        ),
        ValueError,
        TypeError,
    )
    match pc_result:
        case Err(exc):
            return Err(f"config:invariant: phases.constraints.{phase.value} {exc!s} ({source})")
        case Ok(pc):
            return Ok(pc)


def _decimals(values: list[Any], *, key: str, source: str) -> Result[list[Decimal], str]:
    out: list[Decimal] = []
    for i, v in enumerate(values):
        sub_result = _decimal(v, key=f"{key}[{i}]", source=source)
        match sub_result:
            case Err(reason):
                return Err(reason)
            case Ok(d):
                out.append(d)
    return Ok(out)


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
