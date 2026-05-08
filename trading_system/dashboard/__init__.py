"""Read-only dashboard view over ``analytics/``.

Per REQ_SDS_MOD_015 the dashboard SHALL NOT expose any
trade-execution actions; the public surface is the ``render()``
method and the frozen ``DashboardView`` it returns.

REQ refs: REQ_F_DSH_001 (current phase, allocation, turbo exposure,
after-tax performance, drawdown, trade history), REQ_SDS_MOD_015
(read-only over analytics), REQ_C_CLA_002 (LLM / dashboards SHALL
NOT execute trades).
"""

from trading_system.dashboard.engine import Dashboard
from trading_system.dashboard.view import (
    AllocationRow,
    DashboardView,
    TradeHistoryRow,
)

__all__ = [
    "AllocationRow",
    "Dashboard",
    "DashboardView",
    "TradeHistoryRow",
]
