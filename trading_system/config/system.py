"""Loader for ``config/system.yaml``.

Extracted from ``trading_system.main._load_system_config`` so the
centralised ``validate_all`` runner can drive it like every other
loader, and so future runtime code paths (CR-006 multi-account
startup, CR-001 notifications) reach the same single source of
truth.

REQ refs: REQ_O_003 (starting capital + broker from configuration),
REQ_SDS_CFG_001, REQ_SDD_API_004 (frozen Config), REQ_SDD_ERR_002
(categorised Errs).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from trading_system.models.money import Currency, Money
from trading_system.result import Err, Ok, Result, catch


@dataclass(frozen=True, slots=True)
class SystemConfig:
    """Subset of ``system.yaml`` the runtime needs at startup.

    Frozen so callers cannot accidentally mutate it
    (REQ_SDS_INT_004). New fields land here through SRS amendments
    plus an `__post_init__` invariant — never silently.
    """

    starting_capital: Money
    seed: int
    mode: str
    broker_adapter: str

    def __post_init__(self) -> None:
        if self.starting_capital.amount <= 0:
            raise ValueError(
                "SystemConfig.starting_capital must be positive, "
                f"got {self.starting_capital.amount}"
            )
        if self.seed < 0:
            raise ValueError(
                f"SystemConfig.seed must be >= 0, got {self.seed}"
            )
        if self.mode not in ("backtest", "live", "paper"):
            raise ValueError(
                f"SystemConfig.mode must be one of "
                f"['backtest', 'live', 'paper'], got {self.mode!r}"
            )
        if not self.broker_adapter.strip():
            raise ValueError("SystemConfig.broker_adapter must be non-empty")


def load_system_config(path: Path | str) -> Result[SystemConfig, str]:  # noqa: PLR0911
    """Parse ``config/system.yaml``; categorised ``Err`` on any failure."""
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
            raw = parsed

    if not isinstance(raw, Mapping):
        return Err(
            f"config:schema: top-level of {p} must be a mapping, "
            f"got {type(raw).__name__}"
        )

    sys_section = raw.get("system", {})
    broker_section = raw.get("broker", {})
    if not isinstance(sys_section, Mapping):
        return Err(f"config:schema: 'system' section must be a mapping ({p})")
    if not isinstance(broker_section, Mapping):
        return Err(f"config:schema: 'broker' section must be a mapping ({p})")

    capital = sys_section.get("starting_capital", {})
    if not isinstance(capital, Mapping):
        return Err(
            f"config:schema: system.starting_capital must be a mapping ({p})"
        )
    amount = capital.get("amount")
    currency = capital.get("currency")
    if amount is None or currency is None:
        return Err(
            f"config:schema: system.starting_capital "
            f"requires both 'amount' and 'currency' ({p})"
        )
    try:
        cur = Currency(currency)
    except ValueError as e:
        return Err(f"config:invariant: bad currency {currency!r}: {e} ({p})")
    try:
        amount_dec = Decimal(str(amount))
    except (InvalidOperation, ValueError) as e:
        return Err(
            f"config:schema: system.starting_capital.amount "
            f"not Decimal-parseable (value={amount!r}): {e} ({p})"
        )

    seed_raw = sys_section.get("seed", 0)
    if not isinstance(seed_raw, int) or isinstance(seed_raw, bool):
        return Err(
            f"config:schema: system.seed must be int "
            f"(got {type(seed_raw).__name__}) ({p})"
        )
    mode_raw = sys_section.get("mode", "backtest")
    if not isinstance(mode_raw, str):
        return Err(
            f"config:schema: system.mode must be a string "
            f"(got {type(mode_raw).__name__}) ({p})"
        )
    adapter_raw = broker_section.get("adapter", "local")
    if not isinstance(adapter_raw, str):
        return Err(
            f"config:schema: broker.adapter must be a string "
            f"(got {type(adapter_raw).__name__}) ({p})"
        )

    built = catch(
        lambda: SystemConfig(
            starting_capital=Money(amount_dec, cur),
            seed=seed_raw,
            mode=mode_raw,
            broker_adapter=adapter_raw,
        ),
        ValueError,
    )
    match built:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(cfg):
            return Ok(cfg)
