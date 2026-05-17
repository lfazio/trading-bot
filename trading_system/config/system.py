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
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from trading_system.models.money import Currency, Money
from trading_system.result import Err, Ok, Result, catch


@dataclass(frozen=True, slots=True)
class DataProviderConfig:
    """Per-deployment data-provider selection — CR-016 MVP-2.

    ``provider`` selects which ``MarketDataProvider`` the runtime
    instantiates at startup:
      - ``"mock"``  — ``MockMarketDataProvider`` (legacy demo path;
        synthetic universe; zero network).
      - ``"yfinance"`` — ``YFinanceMarketDataProvider`` reading
        from ``cache_root``; auto-populates the cache from
        ``data/yfinance-fixtures/`` when ``bundled_fixtures``
        is true + the cache is empty.

    ``cache_root`` defaults to ``.cache/yfinance/`` matching the
    yfinance recorder's default. Operators with a private dataset
    point at a different root.

    ``bundled_fixtures`` (default ``True``) controls the offline
    fallback path — when set, an empty cache at startup is
    populated from the shipped fixtures so the demo runs without
    network. Disable in production deployments that strictly want
    live recordings.
    """

    provider: str = "mock"
    cache_root: str = ".cache/yfinance"
    bundled_fixtures: bool = True
    universe: str = ""  # optional — MVP-3 universe preset name

    def __post_init__(self) -> None:
        if self.provider not in ("mock", "yfinance"):
            raise ValueError(
                f"DataProviderConfig.provider must be one of "
                f"['mock', 'yfinance'], got {self.provider!r}"
            )
        if not self.cache_root.strip():
            raise ValueError(
                "DataProviderConfig.cache_root must be non-empty"
            )


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
    # CR-016 MVP-2 — data-provider selection. Defaults preserve the
    # pre-MVP-2 demo behaviour (mock provider) so existing
    # deployments are bit-identical.
    data: DataProviderConfig = field(default_factory=DataProviderConfig)

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

    # CR-016 MVP-2 — optional ``data:`` section.
    data_section = raw.get("data", {})
    if not isinstance(data_section, Mapping):
        return Err(
            f"config:schema: 'data' section must be a mapping "
            f"(got {type(data_section).__name__}) ({p})"
        )
    data_kwargs: dict[str, Any] = {}
    if "provider" in data_section:
        v = data_section["provider"]
        if not isinstance(v, str):
            return Err(
                f"config:schema: data.provider must be a string "
                f"(got {type(v).__name__}) ({p})"
            )
        data_kwargs["provider"] = v
    if "cache_root" in data_section:
        v = data_section["cache_root"]
        if not isinstance(v, str):
            return Err(
                f"config:schema: data.cache_root must be a string "
                f"(got {type(v).__name__}) ({p})"
            )
        data_kwargs["cache_root"] = v
    if "bundled_fixtures" in data_section:
        v = data_section["bundled_fixtures"]
        if not isinstance(v, bool):
            return Err(
                f"config:schema: data.bundled_fixtures must be a bool "
                f"(got {type(v).__name__}) ({p})"
            )
        data_kwargs["bundled_fixtures"] = v
    if "universe" in data_section:
        v = data_section["universe"]
        if not isinstance(v, str):
            return Err(
                f"config:schema: data.universe must be a string "
                f"(got {type(v).__name__}) ({p})"
            )
        data_kwargs["universe"] = v
    data_result = catch(lambda: DataProviderConfig(**data_kwargs), ValueError)
    match data_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(data_cfg):
            pass

    built = catch(
        lambda: SystemConfig(
            starting_capital=Money(amount_dec, cur),
            seed=seed_raw,
            mode=mode_raw,
            broker_adapter=adapter_raw,
            data=data_cfg,
        ),
        ValueError,
    )
    match built:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(cfg):
            return Ok(cfg)
