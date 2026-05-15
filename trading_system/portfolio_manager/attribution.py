"""``AttributionDecomposition`` — multi-scope NAV decomposition.

Extends the existing ``portfolio.attribution()`` row-shape with
strategy / sector / class / region scopes. Each scope's values SHALL
sum to household NAV within ``1e-9`` (REQ_SDD_ALG_020 family) — the
invariant is enforced at construction so the dashboard never renders
a stale / inconsistent attribution.

REQ refs: REQ_F_PMG_005, REQ_SDS_PMG_002, REQ_SDD_PMG_004,
REQ_SDD_ALG_020.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from trading_system.models.identifiers import StrategyId
from trading_system.models.instrument import InstrumentClass
from trading_system.models.money import Currency, Money


_TOLERANCE = Decimal("1e-9")


@dataclass(frozen=True, slots=True)
class AttributionDecomposition:
    """Multi-scope decomposition of household NAV.

    Construction enforces the sum-to-NAV invariant on every scope —
    if any scope's values don't sum to the household NAV within
    ``1e-9``, the constructor raises ``ValueError`` mentioning the
    offending scope name.

    The NAV reference is computed from ``by_class`` (the simplest
    decomposition — every position has exactly one class), so an
    operator passing inconsistent input gets a clean error rather
    than a subtly wrong dashboard render.
    """

    by_strategy: Mapping[StrategyId, Money]
    by_sector: Mapping[str, Money]          # 'other' for non-equity
    by_class: Mapping[InstrumentClass, Money]
    by_region: Mapping[str, Money]          # ISO country code

    def __post_init__(self) -> None:
        if not self.by_class:
            raise ValueError(
                "AttributionDecomposition.by_class cannot be empty — "
                "the NAV reference is computed from it"
            )
        nav = _sum_money(self.by_class.values())
        for scope_name, scope in (
            ("by_strategy", self.by_strategy),
            ("by_sector", self.by_sector),
            ("by_class", self.by_class),
            ("by_region", self.by_region),
        ):
            if not scope:
                # Empty scope: zero-NAV portfolio is valid; any other
                # empty scope is inconsistent.
                if nav.amount != Decimal(0):
                    raise ValueError(
                        f"AttributionDecomposition.{scope_name} is empty "
                        f"but NAV is {nav}"
                    )
                continue
            scope_total = _sum_money(scope.values())
            if scope_total.currency != nav.currency:
                raise ValueError(
                    f"AttributionDecomposition.{scope_name} currency "
                    f"{scope_total.currency} differs from NAV currency "
                    f"{nav.currency}"
                )
            if abs(scope_total.amount - nav.amount) > _TOLERANCE:
                raise ValueError(
                    f"AttributionDecomposition.{scope_name} sum "
                    f"{scope_total.amount} differs from NAV "
                    f"{nav.amount} beyond {_TOLERANCE} tolerance"
                )


def attribution_decomposition(
    *,
    by_strategy: Mapping[StrategyId, Money],
    by_sector: Mapping[str, Money],
    by_class: Mapping[InstrumentClass, Money],
    by_region: Mapping[str, Money],
) -> AttributionDecomposition:
    """Construct an ``AttributionDecomposition`` from pre-aggregated
    scopes.

    v1 takes the already-aggregated dictionaries as input — the
    Phase-6 runtime wiring will plug this into a portfolio-aware
    helper that derives the aggregates from positions + trade log.
    Keeping the v1 surface aggregation-only keeps the test surface
    tight and avoids coupling to ``Portfolio`` internals.
    """
    return AttributionDecomposition(
        by_strategy=dict(by_strategy),
        by_sector=dict(by_sector),
        by_class=dict(by_class),
        by_region=dict(by_region),
    )


def _sum_money(values: object) -> Money:
    """Sum a sequence of ``Money`` values; assumes a common currency
    (the caller's invariant). Returns ``Money(0, EUR)`` for an empty
    sequence — the AttributionDecomposition constructor guards
    against the genuinely-empty case."""
    items = list(values)  # type: ignore[arg-type]
    if not items:
        return Money(Decimal(0), Currency.EUR)
    first = items[0]
    total = Money(Decimal(0), first.currency)
    for item in items:
        total = total + item
    return total
