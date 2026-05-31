"""CR-030 — SRD coverage gate.

Enforces Euronext's *couverture* discipline: the portfolio's
coverage value (cash + 80% bonds + 50% held equities) SHALL
exceed 25% × total SRD notional. v1 ships the equity-only
formula since the engine doesn't yet model bonds.

The gate is consulted BEFORE submitting an SRD order, in addition
to the existing cash-equity risk gates. Approaching the floor
(30% headroom or less) emits a DEGRADED KillSwitchTrigger through
the SafetyLayer Protocol so the operator sees the warning + can
react before the hard reject.

REQ refs:
- REQ_F_SRD_004 — gate semantics (25% floor, DEGRADED warning at 30%).
- REQ_SDD_SRD_005 — coverage formula + Err category.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import InstrumentId
from trading_system.models.trading import Order, OrderType
from trading_system.result import Err, Ok, Result


# Configurable thresholds — defaults match REQ_F_SRD_004's
# conservative ceiling. Operators may TIGHTEN via config but SHALL
# NOT loosen below Euronext's official 20% minimum.
DEFAULT_MIN_COVERAGE_RATIO = Decimal("0.25")
DEFAULT_WARN_COVERAGE_RATIO = Decimal("0.30")
# Equity haircut applied to held cash-equity positions when
# computing the coverage value (Euronext's couverture formula
# treats equities as 50% collateral).
EQUITY_COVERAGE_HAIRCUT = Decimal("0.50")


@runtime_checkable
class _SafetyLayerView(Protocol):
    """Subset of ``SafetyLayer`` the gate consults. Decoupled via
    Protocol so the structural audit stays clean — the risk layer
    SHALL NOT import ``safety`` directly."""

    def raise_trigger(self, trigger) -> object: ...


@dataclass(frozen=True, slots=True)
class _DegradedTrigger:
    """In-module trigger shape — passed to the SafetyLayer view
    via duck typing so we avoid importing the safety package."""

    category: str
    code: str
    severity: str
    detail: str


def _coverage_value(
    portfolio,  # type: ignore[no-untyped-def]
    market_prices: dict[InstrumentId, Decimal],
) -> Decimal:
    """REQ_SDD_SRD_005 — coverage = cash + EQUITY_COVERAGE_HAIRCUT
    × Σ(quantity × current_price) for every cash-equity position.
    Bonds are modelled at 80% but v1 ships the equity-only path
    since the engine doesn't yet hold bond positions."""
    cash = portfolio.cash().amount
    equity_marked = Decimal(0)
    for iid, pos in portfolio.positions().items():
        price = market_prices.get(iid)
        if price is None:
            continue
        equity_marked += abs(pos.quantity) * price
    return cash + EQUITY_COVERAGE_HAIRCUT * equity_marked


def _srd_notional_after(
    portfolio,  # type: ignore[no-untyped-def]
    proposal: Order,
    market_prices: dict[InstrumentId, Decimal],
) -> Decimal:
    """REQ_SDD_SRD_005 / REQ_SDD_SRD_009 — SRD notional summed over
    every open SRD position at the latest mark, plus the proposed
    new SRD order's contribution. Non-SRD proposals contribute
    zero."""
    total = Decimal(0)
    for iid, pos in portfolio.srd_positions().items():
        price = market_prices.get(iid)
        if price is None:
            continue
        total += pos.quantity * price
    if proposal.type in (OrderType.SRD_LONG, OrderType.SRD_SHORT):
        proposal_price = market_prices.get(proposal.instrument.id)
        if proposal_price is not None:
            total += proposal.quantity * proposal_price
    return total


def srd_coverage_gate(
    portfolio,  # type: ignore[no-untyped-def]
    proposal: Order,
    market_prices: dict[InstrumentId, Decimal],
    *,
    safety: _SafetyLayerView | None = None,
    min_ratio: Decimal = DEFAULT_MIN_COVERAGE_RATIO,
    warn_ratio: Decimal = DEFAULT_WARN_COVERAGE_RATIO,
) -> Result[None, str]:
    """REQ_F_SRD_004 / REQ_SDD_SRD_005 — pre-trade coverage check.

    Returns ``Ok(None)`` when ``coverage / notional >= min_ratio``.
    Below the floor surfaces ``Err("srd:insufficient_coverage:<ratio>")``.
    Between ``min_ratio`` and ``warn_ratio`` the gate ACCEPTS but
    raises a DEGRADED trigger through ``safety`` (when wired) so
    the operator sees the warning. Non-SRD proposals pass through
    unchanged — the gate is SRD-specific.

    ``min_ratio`` defaults to 0.25 (the conservative Euronext
    couverture floor — official minimum is 0.20). Operators may
    tighten via the optional kwarg but SHALL NOT loosen below 0.20.
    """
    # Non-SRD proposals: skip the gate entirely.
    if proposal.type not in (OrderType.SRD_LONG, OrderType.SRD_SHORT):
        return Ok(None)

    notional = _srd_notional_after(portfolio, proposal, market_prices)
    if notional <= 0:
        # Defensive: no notional ⇒ no exposure to cover.
        return Ok(None)
    coverage = _coverage_value(portfolio, market_prices)
    ratio = coverage / notional if notional > 0 else Decimal(0)

    if ratio < min_ratio:
        return Err(
            f"srd:insufficient_coverage:"
            f"{ratio.quantize(Decimal('0.0001'))}"
        )

    # Between min_ratio (25%) and warn_ratio (30%) — accept + warn.
    if ratio < warn_ratio and safety is not None:
        try:
            safety.raise_trigger(
                _DegradedTrigger(
                    category="STRATEGY",
                    code="srd_coverage_low",
                    severity="DEGRADED",
                    detail=(
                        f"SRD coverage at "
                        f"{ratio.quantize(Decimal('0.0001'))}; "
                        f"warning threshold {warn_ratio}, "
                        f"floor {min_ratio}"
                    ),
                )
            )
        except Exception:  # noqa: BLE001 — defensive
            pass
    return Ok(None)
