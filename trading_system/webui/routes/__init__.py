"""Route handlers.

Phase A shipped two reference handlers; Phase B adds the async
backtest pair + four read-only fan-out endpoints so the stdlib
webui reaches REQ_F_WEB_002 (b/c/d/e) + REQ_F_WEB_003 +
REQ_F_WEB_009 parity with the FastAPI surface:

- ``live_state.build_live_state_handler`` — REQ_F_WEB_002 (a) read.
- ``summary.build_summary_handler`` — REQ_F_WEB_002 (b) financial
  summary read.
- ``registry_list.build_registry_list_handler`` — REQ_F_WEB_002 (c)
  strategy-registry read.
- ``backtests_archive.build_backtests_archive_handler`` —
  REQ_F_WEB_002 (d) backtest-archive paginated read.
- ``improvement_reports_history.build_improvement_reports_history_handler``
  — REQ_F_WEB_002 (e) ImprovementReport history read.
- ``registry_promotion.build_promotion_handler`` — REQ_F_WEB_004
  mutation with idempotency + auth + Phase-B-ready AnomalyAlert
  notification hook.
- ``backtests.build_submit_handler`` — REQ_F_WEB_003 / REQ_F_WEB_009
  enqueue an async backtest; returns 202 + job_id.
- ``backtests.build_status_handler`` — REQ_F_WEB_003 / REQ_F_WEB_009
  read the current status + summary for a job_id.

Each builder takes the dependencies it needs and returns a closure
satisfying the ``server.Handler`` signature
(``Request -> JsonResponse``).
"""

from __future__ import annotations

from trading_system.webui.routes.backtests import (
    JobIdGenerator,
    build_status_handler,
    build_submit_handler,
)
from trading_system.webui.routes.backtests_archive import (
    BacktestsArchiveReader,
    build_backtests_archive_handler,
)
from trading_system.webui.routes.improvement_reports_history import (
    ImprovementReportsHistoryReader,
    build_improvement_reports_history_handler,
)
from trading_system.webui.routes.live_state import (
    LiveStateReader,
    build_live_state_handler,
)
from trading_system.webui.routes.registry_list import (
    RegistryListReader,
    build_registry_list_handler,
)
from trading_system.webui.routes.registry_promotion import (
    PromotionAuditNotifier,
    RegistryPromoter,
    build_promotion_handler,
)
from trading_system.webui.routes.summary import (
    SummaryReader,
    build_summary_handler,
)

__all__ = [
    "BacktestsArchiveReader",
    "ImprovementReportsHistoryReader",
    "JobIdGenerator",
    "LiveStateReader",
    "PromotionAuditNotifier",
    "RegistryListReader",
    "RegistryPromoter",
    "SummaryReader",
    "build_backtests_archive_handler",
    "build_improvement_reports_history_handler",
    "build_live_state_handler",
    "build_promotion_handler",
    "build_registry_list_handler",
    "build_status_handler",
    "build_submit_handler",
    "build_summary_handler",
]
