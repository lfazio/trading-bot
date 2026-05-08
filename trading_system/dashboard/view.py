"""``DashboardView`` and its sub-types.

Every value the operator's dashboard renders sits on this frozen
dataclass. Read-only by construction (frozen + slots); a renderer
takes the view and produces text / HTML / a TUI without ever calling
back into trading state.

REQ refs: REQ_F_DSH_001 (fields enumerated below), REQ_SDS_MOD_015.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.analytics.engine import PerformanceSummary
from trading_system.models.identifiers import InstrumentId, StrategyId, TradeId
from trading_system.models.instrument import InstrumentClass
from trading_system.models.money import Money
from trading_system.models.phase import Phase
from trading_system.portfolio.portfolio import AttributionRow


@dataclass(frozen=True, slots=True)
class AllocationRow:
    """One row of the allocation table."""

    instrument_class: InstrumentClass
    exposure_pct: Decimal


@dataclass(frozen=True, slots=True)
class TradeHistoryRow:
    """One row of the trade history (REQ_F_DSH_001)."""

    trade_id: TradeId
    at: datetime
    instrument_id: InstrumentId
    strategy: StrategyId
    price: Decimal
    quantity_filled: Decimal
    fees: Money


@dataclass(frozen=True, slots=True)
class DashboardView:
    """Single shot of the operator dashboard.

    Fields cover REQ_F_DSH_001:
    - ``phase`` — current phase (1..6).
    - ``allocation`` — per-class exposure table.
    - ``turbo_exposure_pct`` — share of equity in turbos
      (a derived projection of ``allocation`` kept on the view as a
      convenience for the Phase-engine display).
    - ``performance`` — headline metrics (return after tax, drawdown,
      Sharpe, totals).
    - ``trade_history`` — most recent trades, newest last.
    - ``attribution`` — Phase-6 attribution rows (NAV / by strategy /
      by class).
    """

    rendered_at: datetime
    phase: Phase
    allocation: tuple[AllocationRow, ...]
    turbo_exposure_pct: Decimal
    performance: PerformanceSummary
    trade_history: tuple[TradeHistoryRow, ...]
    attribution: tuple[AttributionRow, ...]
