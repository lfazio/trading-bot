"""Loader for ``config/quant.yaml`` — REQ_SDD_QNT_008.

Frozen ``QuantConfig`` aggregating:
- ``ValidatorConfig`` — bounds_table + metric_vocabulary +
  min_duration_days_for_1d + min_window_for_intraday_days
  (REQ_F_QNT_004).
- ``OverfittingConfig`` — ratio_max + ic_floor (REQ_F_QNT_006).

Absent file ⇒ ``Ok(QuantConfig())`` so a deployment without
``config/quant.yaml`` keeps the documented v1 thresholds
(REQ_SDS_CFG_002). Present file fails the C2 startup gate on a bad
shape.

The loader is the **schema gate** only — wiring the resulting
config into the runtime's ``HypothesisValidator`` /
``overfitting_gate`` calls is the meta-loop's job and lands when
CR-002 Phase B rewires ``strategy_lab/loop_controller.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from trading_system.result import Err, Ok, Result, catch
from trading_system.strategy_lab.quant.hypothesis import (
    DEFAULT_METRIC_VOCABULARY,
)
from trading_system.strategy_lab.quant.overfitting import OverfittingConfig
from trading_system.strategy_lab.quant.validator import ValidatorConfig


@dataclass(frozen=True, slots=True)
class QuantConfig:
    """Top-level shape of ``config/quant.yaml``."""

    validator: ValidatorConfig = field(default_factory=ValidatorConfig)
    overfitting: OverfittingConfig = field(default_factory=OverfittingConfig)


_TOP = "quant"


def load_quant_config(path: Path | str) -> Result[QuantConfig, str]:
    """Parse ``config/quant.yaml``.

    Absent file is NOT an error here — the caller checks
    ``path.exists()`` first and falls back to ``QuantConfig()``
    when it doesn't. Empty file ⇒ defaults; missing ``quant:`` top
    key ⇒ defaults.
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

    if payload is None:
        return Ok(QuantConfig())
    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {p} must be a mapping, "
            f"got {type(payload).__name__}"
        )
    section = payload.get(_TOP)
    if section is None:
        return Ok(QuantConfig())
    if not isinstance(section, Mapping):
        return Err(
            f"config:schema: '{_TOP}' section must be a mapping, "
            f"got {type(section).__name__} ({p})"
        )

    # ---------- validator -------------------------------------------------
    validator_kwargs: dict[str, Any] = {}
    validator_raw = section.get("validator")
    if validator_raw is not None:
        if not isinstance(validator_raw, Mapping):
            return Err(
                f"config:schema: quant.validator must be a mapping ({p})"
            )
        # bounds_table — mapping[str, {lo, hi}]
        if "bounds_table" in validator_raw:
            bt_raw = validator_raw["bounds_table"]
            if not isinstance(bt_raw, Mapping):
                return Err(
                    f"config:schema: quant.validator.bounds_table must "
                    f"be a mapping ({p})"
                )
            bt: dict[str, tuple[Decimal, Decimal]] = {}
            for metric, bounds_raw in bt_raw.items():
                if not isinstance(metric, str):
                    return Err(
                        f"config:schema: quant.validator.bounds_table key "
                        f"must be a string, got {type(metric).__name__} ({p})"
                    )
                if not isinstance(bounds_raw, Mapping):
                    return Err(
                        f"config:schema: quant.validator.bounds_table[{metric}] "
                        f"must be a mapping with lo + hi ({p})"
                    )
                lo_raw = bounds_raw.get("lo")
                hi_raw = bounds_raw.get("hi")
                if lo_raw is None or hi_raw is None:
                    return Err(
                        f"config:schema: quant.validator.bounds_table[{metric}] "
                        f"must carry both 'lo' and 'hi' ({p})"
                    )
                try:
                    lo = Decimal(str(lo_raw))
                    hi = Decimal(str(hi_raw))
                except (InvalidOperation, ValueError):
                    return Err(
                        f"config:schema: quant.validator.bounds_table[{metric}] "
                        f"lo/hi must be Decimal-parseable "
                        f"(got {lo_raw!r} / {hi_raw!r}) ({p})"
                    )
                if lo > hi:
                    return Err(
                        f"config:invariant: quant.validator.bounds_table[{metric}] "
                        f"lo ({lo}) must be <= hi ({hi}) ({p})"
                    )
                bt[metric] = (lo, hi)
            validator_kwargs["bounds_table"] = bt
        # metric_vocabulary — list[str]
        if "metric_vocabulary" in validator_raw:
            mv_raw = validator_raw["metric_vocabulary"]
            if not isinstance(mv_raw, list) or not all(
                isinstance(m, str) for m in mv_raw
            ):
                return Err(
                    f"config:schema: quant.validator.metric_vocabulary "
                    f"must be a list of strings ({p})"
                )
            if not mv_raw:
                return Err(
                    f"config:invariant: quant.validator.metric_vocabulary "
                    f"must be non-empty ({p})"
                )
            validator_kwargs["metric_vocabulary"] = frozenset(mv_raw)
        # min_duration_days_for_1d
        if "min_duration_days_for_1d" in validator_raw:
            v = validator_raw["min_duration_days_for_1d"]
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                return Err(
                    f"config:schema: quant.validator.min_duration_days_for_1d "
                    f"must be a positive int (got {v!r}) ({p})"
                )
            validator_kwargs["min_duration_days_for_1d"] = v
        # min_window_for_intraday_days
        if "min_window_for_intraday_days" in validator_raw:
            v = validator_raw["min_window_for_intraday_days"]
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                return Err(
                    f"config:schema: quant.validator.min_window_for_intraday_days "
                    f"must be a positive int (got {v!r}) ({p})"
                )
            validator_kwargs["min_window_for_intraday_days"] = v

    validator_result = catch(
        lambda: ValidatorConfig(**validator_kwargs), ValueError
    )
    match validator_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(validator):
            pass

    # ---------- overfitting ----------------------------------------------
    overfitting_kwargs: dict[str, Any] = {}
    overfitting_raw = section.get("overfitting")
    if overfitting_raw is not None:
        if not isinstance(overfitting_raw, Mapping):
            return Err(
                f"config:schema: quant.overfitting must be a mapping ({p})"
            )
        for key in ("ratio_max", "ic_floor"):
            if key in overfitting_raw:
                v = overfitting_raw[key]
                if isinstance(v, bool):
                    return Err(
                        f"config:schema: quant.overfitting.{key} must be "
                        f"numeric, got bool ({p})"
                    )
                try:
                    overfitting_kwargs[key] = Decimal(str(v))
                except (InvalidOperation, ValueError):
                    return Err(
                        f"config:schema: quant.overfitting.{key} could not "
                        f"be parsed as Decimal (value={v!r}) ({p})"
                    )
    overfitting_result = catch(
        lambda: OverfittingConfig(**overfitting_kwargs), ValueError
    )
    match overfitting_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(overfitting):
            pass

    return Ok(QuantConfig(validator=validator, overfitting=overfitting))


# Re-export so callers can construct a default QuantConfig without
# importing the underlying types directly.
__all__ = [
    "DEFAULT_METRIC_VOCABULARY",
    "OverfittingConfig",
    "QuantConfig",
    "ValidatorConfig",
    "load_quant_config",
]
