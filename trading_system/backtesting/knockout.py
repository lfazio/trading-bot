"""``KnockoutSimulator`` — close turbo positions at zero on barrier breach.

REQ refs:
- REQ_F_BCT_004 — knockouts close the position at zero when the
  underlying breaches the barrier.
- REQ_F_TRB_005 — turbo loss is capped at the invested capital
  (cost basis); the realization is a full loss of the cost basis.

A LONG turbo knocks out when the underlying's ``last`` <= ``knockout``;
a SHORT turbo knocks out when ``last`` >= ``knockout``. The simulator
checks every open turbo against the just-arrived tick's price.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_system.execution.types import Tick
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Turbo
from trading_system.portfolio.portfolio import Portfolio
from trading_system.tax.config import TaxConfig


@dataclass(slots=True)
class KnockoutSimulator:
    """Stateless knockout evaluator (no per-instrument bookkeeping —
    closure is recorded on the Portfolio)."""

    def maybe_trigger(
        self,
        tick: Tick,
        portfolio: Portfolio,
        tax: TaxConfig,
    ) -> list[InstrumentId]:
        """Close every turbo whose underlying matches ``tick`` and
        whose barrier is breached.

        Returns the list of closed instrument ids.

        ``tick`` carries the **underlying's** instrument id; turbo
        positions reference the underlying via ``Turbo.underlying``,
        so we scan all open turbo positions and check whether any
        track the same underlying.
        """
        closed: list[InstrumentId] = []
        for iid, pos in portfolio.positions().items():
            if pos.instrument.cls is not InstrumentClass.TURBO:
                continue
            assert isinstance(pos.instrument, Turbo), (
                f"InstrumentClass.TURBO position must be a Turbo, got {type(pos.instrument)}"
            )
            turbo: Turbo = pos.instrument
            if turbo.underlying != tick.instrument_id:
                continue
            if _knockout_breached(turbo, tick.last):
                portfolio.close_at_zero(iid, tax)
                closed.append(iid)
        return closed


def _knockout_breached(turbo: Turbo, price) -> bool:
    """LONG: breach when price falls to/below knockout.
    SHORT: breach when price rises to/above knockout."""
    if turbo.direction == "LONG":
        return price <= turbo.knockout
    return price >= turbo.knockout
