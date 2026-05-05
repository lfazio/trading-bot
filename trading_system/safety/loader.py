"""YAML loader for ``config/kill_switch.yaml``.

Loads the trigger thresholds the monitor uses (single-day loss,
rapid decline, execution rejection rate, slippage anomaly sigma,
manual-recovery requirement) into a frozen
``KillSwitchTriggerConfig``.

REQ refs: REQ_S_KS_010 (loaded once at startup; runtime mutation
unreachable), REQ_SDS_CFG_003, REQ_SDD_API_004 (frozen Config),
REQ_SDD_ERR_002 (categorized errors), REQ_SDD_IMP_006 (loader is
the I/O boundary).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from trading_system.result import Err, Ok, Result, catch


@dataclass(frozen=True, slots=True)
class FinancialTriggerConfig:
    single_day_loss: Decimal = Decimal("0.05")  # REQ_SDD_ALG_006
    rapid_decline_pct: Decimal = Decimal("0.10")  # REQ_SDD_ALG_007
    rapid_decline_days: int = 5

    def __post_init__(self) -> None:
        if not (Decimal(0) < self.single_day_loss <= Decimal(1)):
            raise ValueError(
                f"FinancialTriggerConfig.single_day_loss must lie in (0, 1], "
                f"got {self.single_day_loss}"
            )
        if not (Decimal(0) < self.rapid_decline_pct <= Decimal(1)):
            raise ValueError(
                f"FinancialTriggerConfig.rapid_decline_pct must lie in (0, 1], "
                f"got {self.rapid_decline_pct}"
            )
        if self.rapid_decline_days <= 0:
            raise ValueError(
                f"FinancialTriggerConfig.rapid_decline_days must be > 0, "
                f"got {self.rapid_decline_days}"
            )


@dataclass(frozen=True, slots=True)
class ExecutionTriggerConfig:
    rejection_threshold: Decimal = Decimal("0.20")
    slippage_anomaly_sigma: Decimal = Decimal("3.0")

    def __post_init__(self) -> None:
        if not (Decimal(0) < self.rejection_threshold <= Decimal(1)):
            raise ValueError(
                f"ExecutionTriggerConfig.rejection_threshold must lie in (0, 1], "
                f"got {self.rejection_threshold}"
            )
        if self.slippage_anomaly_sigma <= 0:
            raise ValueError(
                f"ExecutionTriggerConfig.slippage_anomaly_sigma must be > 0, "
                f"got {self.slippage_anomaly_sigma}"
            )


@dataclass(frozen=True, slots=True)
class KillSwitchTriggerConfig:
    """Top-level container — the runtime sees this once at startup."""

    financial: FinancialTriggerConfig = field(default_factory=FinancialTriggerConfig)
    execution: ExecutionTriggerConfig = field(default_factory=ExecutionTriggerConfig)
    require_manual_recovery: bool = True


_TOP = "kill_switch"


def load_kill_switch_config(path: Path | str) -> Result[KillSwitchTriggerConfig, str]:
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


def _build(  # noqa: PLR0911, PLR0912, PLR0915 - linear validator chain
    section: Mapping[str, Any], *, source: str
) -> Result[KillSwitchTriggerConfig, str]:
    fin_raw = section.get("financial", {})
    if not isinstance(fin_raw, Mapping):
        return Err(f"config:schema: kill_switch.financial must be a mapping ({source})")

    fin_kwargs: dict[str, Any] = {}
    if "single_day_loss" in fin_raw:
        d = _decimal(
            fin_raw["single_day_loss"],
            key="kill_switch.financial.single_day_loss",
            source=source,
        )
        match d:
            case Err(reason):
                return Err(reason)
            case Ok(value):
                fin_kwargs["single_day_loss"] = value
    rapid_raw = fin_raw.get("rapid_decline")
    if rapid_raw is not None:
        if not isinstance(rapid_raw, Mapping):
            return Err(
                f"config:schema: kill_switch.financial.rapid_decline must be a mapping ({source})"
            )
        if "pct" in rapid_raw:
            d = _decimal(
                rapid_raw["pct"],
                key="kill_switch.financial.rapid_decline.pct",
                source=source,
            )
            match d:
                case Err(reason):
                    return Err(reason)
                case Ok(value):
                    fin_kwargs["rapid_decline_pct"] = value
        if "days" in rapid_raw:
            days_raw = rapid_raw["days"]
            if not isinstance(days_raw, int) or isinstance(days_raw, bool):
                return Err(
                    f"config:schema: kill_switch.financial.rapid_decline.days "
                    f"must be int (got {type(days_raw).__name__}) ({source})"
                )
            fin_kwargs["rapid_decline_days"] = days_raw

    fin_result = catch(lambda: FinancialTriggerConfig(**fin_kwargs), ValueError)
    match fin_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({source})")
        case Ok(financial):
            pass

    exec_raw = section.get("execution", {})
    if not isinstance(exec_raw, Mapping):
        return Err(f"config:schema: kill_switch.execution must be a mapping ({source})")
    exec_kwargs: dict[str, Any] = {}
    if "rejection_threshold" in exec_raw:
        d = _decimal(
            exec_raw["rejection_threshold"],
            key="kill_switch.execution.rejection_threshold",
            source=source,
        )
        match d:
            case Err(reason):
                return Err(reason)
            case Ok(value):
                exec_kwargs["rejection_threshold"] = value
    if "slippage_anomaly_sigma" in exec_raw:
        d = _decimal(
            exec_raw["slippage_anomaly_sigma"],
            key="kill_switch.execution.slippage_anomaly_sigma",
            source=source,
        )
        match d:
            case Err(reason):
                return Err(reason)
            case Ok(value):
                exec_kwargs["slippage_anomaly_sigma"] = value

    exec_result = catch(lambda: ExecutionTriggerConfig(**exec_kwargs), ValueError)
    match exec_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({source})")
        case Ok(execution):
            pass

    recovery_raw = section.get("recovery", {})
    if not isinstance(recovery_raw, Mapping):
        return Err(f"config:schema: kill_switch.recovery must be a mapping ({source})")
    require_manual = recovery_raw.get("require_manual_token", True)
    if not isinstance(require_manual, bool):
        return Err(
            f"config:schema: kill_switch.recovery.require_manual_token must be "
            f"bool (got {type(require_manual).__name__}) ({source})"
        )

    return Ok(
        KillSwitchTriggerConfig(
            financial=financial,
            execution=execution,
            require_manual_recovery=require_manual,
        )
    )


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
