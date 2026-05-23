"""Universe → default-instrument resolver for the onboarding wizard.

Lives under ``webapp/runtimes/`` (not ``webapp/routers/views/``)
because the structural audit forbids the view tier from
importing ``trading_system.data.*``. The runtimes layer is the
documented carve-out for engine-side reach.

Pure function: takes a universe name + a fallback dict, returns
the first stock the universe YAML lists (alphabetically — the
loader sorts on the way in). Falls back to the supplied default
on any loader failure so a broken YAML doesn't break onboarding.
"""

from __future__ import annotations

from pathlib import Path

import yaml as _yaml

from trading_system.data.universes import DEFAULT_UNIVERSE_ROOT, load_universe
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency


def first_instrument_or_fallback(
    universe: str,
    *,
    fallback: Stock,
) -> Stock:
    """Return the first stock listed in the universe YAML, or
    ``fallback`` when the loader fails / returns an empty list."""
    try:
        result = load_universe(universe)
    except Exception:  # noqa: BLE001 — defensive
        return fallback
    if hasattr(result, "is_ok") and result.is_ok():
        uni = result.unwrap()
        if uni.stocks:
            return uni.stocks[0]
    return fallback


def index_for_universe(
    universe: str,
    *,
    universe_root: Path | None = None,
) -> Stock | None:
    """Return the first index declared in the universe YAML's
    ``indices:`` list, wrapped as a ``Stock`` with the synthetic
    ``INDEX`` exchange so it threads through
    ``yahoo_symbol_for``. Returns ``None`` when the YAML has no
    ``indices`` key or the loader fails.
    """
    root = universe_root if universe_root is not None else DEFAULT_UNIVERSE_ROOT
    path = root / f"{universe}.yaml"
    try:
        raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, _yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    indices = raw.get("indices") or []
    if not isinstance(indices, list) or not indices:
        return None
    first = indices[0]
    if not isinstance(first, dict):
        return None
    idx_id = first.get("id")
    if not isinstance(idx_id, str) or not idx_id:
        return None
    currency_raw = first.get("currency", "EUR")
    try:
        currency = Currency(currency_raw)
    except ValueError:
        currency = Currency.EUR
    domain_id = idx_id.lstrip("^") or idx_id
    return Stock(
        id=InstrumentId(idx_id),
        symbol=idx_id,
        exchange="INDEX",
        currency=currency,
        cls=InstrumentClass.STOCK,
        isin=f"INDEX_{domain_id}",
        sector="index",
        country=first.get("country") or "FR",
    )
