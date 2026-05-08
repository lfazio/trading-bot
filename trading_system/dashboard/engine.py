"""``Dashboard`` — read-only renderer over ``Analytics``.

Construction takes an ``Analytics`` and the ambient phase. The single
public method, ``render(at)``, returns a frozen ``DashboardView``.
There is no ``submit``, ``cancel``, ``place_order``, or any other
trade-execution-shaped method on this class — REQ_SDS_MOD_015
forbids them; ``tests/dashboard/test_engine.py::test_no_trade_actions``
verifies via introspection.

REQ refs: REQ_F_DSH_001, REQ_SDS_MOD_015, REQ_C_CLA_002,
REQ_SDS_FLO_001 (the trade pipeline runs through tax -> risk ->
safety -> broker — dashboards have no place in it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_system.analytics.engine import Analytics
from trading_system.dashboard.view import (
    AllocationRow,
    DashboardView,
    TradeHistoryRow,
)
from trading_system.models.identifiers import InstrumentId, OrderId, StrategyId
from trading_system.models.phase import AllocationBucket, Phase

# Default cap on the trade-history slice. Can be overridden per call;
# protects the operator from rendering 50_000 rows on a long-running
# system.
_DEFAULT_HISTORY_LIMIT = 200


@dataclass(slots=True)
class Dashboard:
    """Read-only display layer.

    The ``orders`` map (``OrderId -> StrategyId``) lets the dashboard
    label trade-history rows with the originating strategy. Live
    deployments populate it from the broker's order journal; backtests
    can reconstruct it from the engine's ``_orders`` map.
    """

    analytics: Analytics
    phase: Phase
    orders: dict[OrderId, StrategyId]

    def render(self, at: datetime, *, history_limit: int = _DEFAULT_HISTORY_LIMIT) -> DashboardView:
        if history_limit <= 0:
            raise ValueError(f"Dashboard.render history_limit must be > 0, got {history_limit}")
        allocation = tuple(
            AllocationRow(instrument_class=cls, exposure_pct=pct)
            for cls, pct in self.analytics.exposure_by_class().items()
        )
        turbo = self.analytics.portfolio.exposure_pct(AllocationBucket.TURBO)
        trades = self.analytics.trades
        sliced = trades[-history_limit:]
        history = tuple(
            TradeHistoryRow(
                trade_id=t.id,
                at=t.executed_at,
                instrument_id=_placeholder_instrument_id(t.order_id),
                strategy=self.orders.get(t.order_id, StrategyId("")),
                price=t.price,
                quantity_filled=t.quantity_filled,
                fees=t.fees,
            )
            for t in sliced
        )
        return DashboardView(
            rendered_at=at,
            phase=self.phase,
            allocation=allocation,
            turbo_exposure_pct=turbo,
            performance=self.analytics.summary(),
            trade_history=history,
            attribution=self.analytics.attribution(),
        )


def _placeholder_instrument_id(order_id: OrderId) -> InstrumentId:
    """Display-only placeholder for the trade's instrument id.

    ``Trade`` carries only ``order_id``; the originating Order is held
    by the engine. Surfacing the ``order_id`` here is preferable to
    silently losing the link until a future ``Trade`` enrichment lands.
    """
    return InstrumentId(f"order:{order_id}")
