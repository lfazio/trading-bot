"""Loader for ``config/notifications.yaml`` — REQ_SDD_NOT_008.

Frozen ``NotificationsConfig`` covering the operator-tunable knobs
the Phase A notification surface needs:

- ``channels`` — closed v1 set ``{"local_log"}``; Phase B extends
  to ``email``, ``whatsapp``.
- ``retry`` — ``max_attempts`` / ``base_delay_seconds`` /
  ``growth_factor`` mirroring ``RetryPolicy``.
- ``approval`` — ``timeout_seconds`` / ``threshold_amount`` /
  ``threshold_currency``.
- ``local_log_path`` — file path the ``LocalLogChannel`` writes to.

Absent file ⇒ ``Ok(NotificationsConfig())`` so a deployment without
``config/notifications.yaml`` keeps using built-in defaults
(REQ_SDS_CFG_002). Present file fails the C2 startup gate on a bad
shape.

The loader is the **schema gate** only — wiring the resulting
config into a concrete ``NotificationFanOut`` / ``LocalLogChannel``
/ ``ApprovalGate`` is the runtime's job and lands when CR-001 Phase
B rewires ``safety/alert_system.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from trading_system.models.money import Currency
from trading_system.result import Err, Ok, Result, catch


# Closed set of channel selectors recognised by v1. Phase B extends.
_CHANNEL_SELECTORS: frozenset[str] = frozenset({"local_log"})

_VALID_CURRENCIES: frozenset[str] = frozenset(c.value for c in Currency)


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Mirrors ``notifications.fanout.RetryPolicy``."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    growth_factor: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError(
                f"RetryConfig.max_attempts must be > 0, got {self.max_attempts}"
            )
        if self.base_delay_seconds < 0:
            raise ValueError(
                f"RetryConfig.base_delay_seconds must be >= 0, "
                f"got {self.base_delay_seconds}"
            )
        if self.growth_factor < 1.0:
            raise ValueError(
                f"RetryConfig.growth_factor must be >= 1.0, "
                f"got {self.growth_factor}"
            )


@dataclass(frozen=True, slots=True)
class ApprovalConfig:
    """Trade-approval gate knobs — REQ_F_NOT_004."""

    timeout_seconds: int = 60
    threshold_amount: Decimal = Decimal("0")
    threshold_currency: str = "EUR"

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"ApprovalConfig.timeout_seconds must be > 0, "
                f"got {self.timeout_seconds}"
            )
        if self.threshold_amount < 0:
            raise ValueError(
                f"ApprovalConfig.threshold_amount must be >= 0, "
                f"got {self.threshold_amount}"
            )
        if self.threshold_currency not in _VALID_CURRENCIES:
            raise ValueError(
                f"ApprovalConfig.threshold_currency must be one of "
                f"{sorted(_VALID_CURRENCIES)}, got {self.threshold_currency!r}"
            )


@dataclass(frozen=True, slots=True)
class NotificationsConfig:
    """Top-level shape of ``config/notifications.yaml``."""

    channels: tuple[str, ...] = ("local_log",)
    retry: RetryConfig = field(default_factory=RetryConfig)
    approval: ApprovalConfig = field(default_factory=ApprovalConfig)
    local_log_path: str = "var/logs/notifications.jsonl"

    def __post_init__(self) -> None:
        if not self.channels:
            raise ValueError(
                "NotificationsConfig.channels must list at least one channel "
                "(LocalLogChannel is the always-available baseline)"
            )
        for ch in self.channels:
            if ch not in _CHANNEL_SELECTORS:
                raise ValueError(
                    f"NotificationsConfig.channels[{ch!r}] not in "
                    f"{sorted(_CHANNEL_SELECTORS)}"
                )
        seen: set[str] = set()
        for ch in self.channels:
            if ch in seen:
                raise ValueError(
                    f"NotificationsConfig.channels has duplicate {ch!r}"
                )
            seen.add(ch)
        if not self.local_log_path.strip():
            raise ValueError(
                "NotificationsConfig.local_log_path must be non-empty"
            )


_TOP = "notifications"


def load_notifications_config(
    path: Path | str,
) -> Result[NotificationsConfig, str]:
    """Parse ``config/notifications.yaml``.

    Absent file is NOT an error here — the caller checks ``path.exists()``
    first and falls back to ``NotificationsConfig()`` when it
    doesn't. This loader only fires when the operator has supplied
    a YAML. Empty file ⇒ defaults; missing ``notifications:`` top
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
        return Ok(NotificationsConfig())
    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {p} must be a mapping, "
            f"got {type(payload).__name__}"
        )
    section = payload.get(_TOP)
    if section is None:
        return Ok(NotificationsConfig())
    if not isinstance(section, Mapping):
        return Err(
            f"config:schema: '{_TOP}' section must be a mapping, "
            f"got {type(section).__name__} ({p})"
        )

    # ---------- channels --------------------------------------------------
    channels_kwargs: dict[str, Any] = {}
    if "channels" in section:
        ch_raw = section["channels"]
        if not isinstance(ch_raw, list) or not all(
            isinstance(c, str) for c in ch_raw
        ):
            return Err(
                f"config:schema: notifications.channels must be a list of "
                f"strings ({p})"
            )
        channels_kwargs["channels"] = tuple(ch_raw)

    # ---------- retry -----------------------------------------------------
    retry_kwargs: dict[str, Any] = {}
    retry_raw = section.get("retry")
    if retry_raw is not None:
        if not isinstance(retry_raw, Mapping):
            return Err(
                f"config:schema: notifications.retry must be a mapping "
                f"({p})"
            )
        if "max_attempts" in retry_raw:
            v = retry_raw["max_attempts"]
            if not isinstance(v, int) or isinstance(v, bool):
                return Err(
                    f"config:schema: notifications.retry.max_attempts must "
                    f"be int (got {type(v).__name__}) ({p})"
                )
            retry_kwargs["max_attempts"] = v
        if "base_delay_seconds" in retry_raw:
            v = retry_raw["base_delay_seconds"]
            if isinstance(v, bool) or not isinstance(v, (int, float)):  # noqa: UP038
                return Err(
                    f"config:schema: notifications.retry.base_delay_seconds "
                    f"must be numeric (got {type(v).__name__}) ({p})"
                )
            retry_kwargs["base_delay_seconds"] = float(v)
        if "growth_factor" in retry_raw:
            v = retry_raw["growth_factor"]
            if isinstance(v, bool) or not isinstance(v, (int, float)):  # noqa: UP038
                return Err(
                    f"config:schema: notifications.retry.growth_factor must "
                    f"be numeric (got {type(v).__name__}) ({p})"
                )
            retry_kwargs["growth_factor"] = float(v)
    retry_result = catch(lambda: RetryConfig(**retry_kwargs), ValueError)
    match retry_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(retry):
            pass

    # ---------- approval --------------------------------------------------
    approval_kwargs: dict[str, Any] = {}
    approval_raw = section.get("approval")
    if approval_raw is not None:
        if not isinstance(approval_raw, Mapping):
            return Err(
                f"config:schema: notifications.approval must be a mapping "
                f"({p})"
            )
        if "timeout_seconds" in approval_raw:
            v = approval_raw["timeout_seconds"]
            if not isinstance(v, int) or isinstance(v, bool):
                return Err(
                    f"config:schema: notifications.approval.timeout_seconds "
                    f"must be int (got {type(v).__name__}) ({p})"
                )
            approval_kwargs["timeout_seconds"] = v
        if "threshold_amount" in approval_raw:
            v = approval_raw["threshold_amount"]
            if isinstance(v, bool):
                return Err(
                    f"config:schema: notifications.approval.threshold_amount "
                    f"must be numeric, got bool ({p})"
                )
            try:
                approval_kwargs["threshold_amount"] = Decimal(str(v))
            except (InvalidOperation, ValueError):
                return Err(
                    f"config:schema: notifications.approval.threshold_amount "
                    f"could not be parsed as Decimal (value={v!r}) ({p})"
                )
        if "threshold_currency" in approval_raw:
            v = approval_raw["threshold_currency"]
            if not isinstance(v, str):
                return Err(
                    f"config:schema: notifications.approval.threshold_currency "
                    f"must be a string (got {type(v).__name__}) ({p})"
                )
            approval_kwargs["threshold_currency"] = v
    approval_result = catch(
        lambda: ApprovalConfig(**approval_kwargs), ValueError
    )
    match approval_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(approval):
            pass

    # ---------- local_log_path -------------------------------------------
    log_path_kwargs: dict[str, Any] = {}
    if "local_log_path" in section:
        v = section["local_log_path"]
        if not isinstance(v, str):
            return Err(
                f"config:schema: notifications.local_log_path must be a "
                f"string (got {type(v).__name__}) ({p})"
            )
        log_path_kwargs["local_log_path"] = v

    built = catch(
        lambda: NotificationsConfig(
            retry=retry,
            approval=approval,
            **channels_kwargs,
            **log_path_kwargs,
        ),
        ValueError,
    )
    match built:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(cfg):
            return Ok(cfg)
