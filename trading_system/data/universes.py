"""Bundled universe presets — MVP-3 of CR-016.

Operators ask for a "universe" (a named list of tradeable
instruments) via the CLI (`trading-bot backtest --universe
eu-dividend-starter`) + the yfinance recorder
(`tools/yfinance_recorder.py --universe eu-dividend-starter`).
The universe loader parses the bundled YAML preset + returns a
frozen `Universe` carrying the `Stock` rows the runtime expects.

Layout:
    data/universes/<name>.yaml

Each YAML lists stocks compatible with `Stock(id, symbol,
exchange, currency, isin, sector, country, cls=InstrumentClass.STOCK)`.
The loader fails-fast with categorised Errs on malformed YAML so
the C2 startup gate (REQ_SDS_CFG_001) catches typos before any
backtest runs.

REQ refs: CR-016 / MVP-3 critical path; REQ_O_004 (operator CLI
consumes universe presets); REQ_NF_DET_001 (alphabetical Stock
ordering for replay determinism).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err, Ok, Result, catch


# Default universe-preset root relative to the repo root.
DEFAULT_UNIVERSE_ROOT: Path = (
    Path(__file__).resolve().parent.parent.parent / "data" / "universes"
)


@dataclass(frozen=True, slots=True)
class Universe:
    """A named, deterministic ordering of `Stock` rows the runtime
    consumes for screening + backtesting.

    Iteration order SHALL be alphabetical by `Stock.id` so two
    backtests against the same preset replay bit-identically
    (REQ_NF_DET_001 family).
    """

    name: str
    description: str
    stocks: tuple[Stock, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Universe.name must be non-empty")
        if not self.stocks:
            raise ValueError(f"Universe {self.name!r} must list at least one stock")
        ids_sorted = sorted(self.stocks, key=lambda s: str(s.id))
        if list(self.stocks) != ids_sorted:
            raise ValueError(
                f"Universe {self.name!r}.stocks must be sorted alphabetically by id "
                "for replay determinism"
            )
        seen: set[str] = set()
        for s in self.stocks:
            if str(s.id) in seen:
                raise ValueError(
                    f"Universe {self.name!r} has duplicate stock id {s.id!r}"
                )
            seen.add(str(s.id))


def load_universe(
    name: str, *, universe_root: Path | None = None
) -> Result[Universe, str]:
    """Load a bundled universe preset by name.

    Categorised Errs:
      config:io:<reason>           — file unreadable / not found
      config:parse:<reason>        — malformed YAML
      config:schema:<reason>       — bad top-level shape / missing field
      config:invariant:<reason>    — Stock / Universe invariant trip
    """
    if not name.strip():
        return Err("config:schema: universe name must be non-empty")
    root = universe_root if universe_root is not None else DEFAULT_UNIVERSE_ROOT
    path = root / f"{name}.yaml"
    raw_result = catch(lambda: path.read_text(encoding="utf-8"), OSError)
    match raw_result:
        case Err(exc):
            return Err(f"config:io: cannot read {path}: {exc!r}")
        case Ok(text):
            raw_text = text

    parsed_result: Result[Any, BaseException] = catch(
        lambda: yaml.safe_load(raw_text), yaml.YAMLError
    )
    match parsed_result:
        case Err(exc):
            return Err(f"config:parse: invalid YAML at {path}: {exc!r}")
        case Ok(parsed):
            payload = parsed

    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {path} must be a mapping "
            f"(got {type(payload).__name__})"
        )

    yaml_name = payload.get("name")
    if not isinstance(yaml_name, str) or yaml_name != name:
        return Err(
            f"config:schema: universe.name in {path} must match the file "
            f"name {name!r} (got {yaml_name!r})"
        )
    description = payload.get("description", "")
    if not isinstance(description, str):
        return Err(
            f"config:schema: universe.description must be a string "
            f"(got {type(description).__name__}) ({path})"
        )
    stocks_raw = payload.get("stocks")
    if not isinstance(stocks_raw, list):
        return Err(
            f"config:schema: universe.stocks must be a list "
            f"(got {type(stocks_raw).__name__}) ({path})"
        )

    stocks: list[Stock] = []
    for i, item in enumerate(stocks_raw):
        if not isinstance(item, Mapping):
            return Err(
                f"config:schema: universe.stocks[{i}] must be a mapping "
                f"(got {type(item).__name__}) ({path})"
            )
        stock_result = _build_stock(item, index=i, source=path)
        match stock_result:
            case Err(reason):
                return Err(reason)
            case Ok(s):
                stocks.append(s)

    # Universe enforces alphabetical-by-id ordering; sort here so
    # YAML order doesn't need to match.
    stocks.sort(key=lambda s: str(s.id))

    built = catch(
        lambda: Universe(name=name, description=description, stocks=tuple(stocks)),
        ValueError,
    )
    match built:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({path})")
        case Ok(uni):
            return Ok(uni)


def list_bundled_universes(
    *, universe_root: Path | None = None
) -> Result[tuple[str, ...], str]:
    """Return the bundled universe names (alphabetical) discovered
    under ``universe_root``. Useful for the CLI's
    ``trading-bot record-data --universe`` preview + validation.
    """
    root = universe_root if universe_root is not None else DEFAULT_UNIVERSE_ROOT
    if not root.is_dir():
        return Err(f"config:io: universe root {root} is not a directory")
    return Ok(tuple(sorted(p.stem for p in root.glob("*.yaml"))))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_REQUIRED_STOCK_FIELDS = (
    "id",
    "symbol",
    "exchange",
    "currency",
    "isin",
    "sector",
    "country",
)


def _build_stock(
    item: Mapping[str, Any], *, index: int, source: Path
) -> Result[Stock, str]:
    """Parse one stock entry, returning a Stock or a categorised Err."""
    missing = [f for f in _REQUIRED_STOCK_FIELDS if f not in item]
    if missing:
        return Err(
            f"config:schema: universe.stocks[{index}] missing required "
            f"field(s) {missing} ({source})"
        )
    raw_id = item["id"]
    if not isinstance(raw_id, str) or not raw_id.strip():
        return Err(
            f"config:schema: universe.stocks[{index}].id must be a "
            f"non-empty string (got {raw_id!r}) ({source})"
        )
    raw_currency = item["currency"]
    if not isinstance(raw_currency, str):
        return Err(
            f"config:schema: universe.stocks[{index}].currency must be a "
            f"string (got {type(raw_currency).__name__}) ({source})"
        )
    try:
        currency = Currency(raw_currency)
    except ValueError as e:
        return Err(
            f"config:invariant: universe.stocks[{index}].currency: {e} ({source})"
        )
    for field_name in ("symbol", "exchange", "isin", "sector", "country"):
        v = item[field_name]
        if not isinstance(v, str):
            return Err(
                f"config:schema: universe.stocks[{index}].{field_name} must "
                f"be a string (got {type(v).__name__}) ({source})"
            )
    try:
        return Ok(
            Stock(
                id=InstrumentId(raw_id),
                symbol=item["symbol"],
                exchange=item["exchange"],
                currency=currency,
                cls=InstrumentClass.STOCK,
                isin=item["isin"],
                sector=item["sector"],
                country=item["country"],
            )
        )
    except ValueError as e:
        return Err(
            f"config:invariant: universe.stocks[{index}]: {e} ({source})"
        )
