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

from trading_system.data.universes import load_universe
from trading_system.models.instrument import Stock


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
