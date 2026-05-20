"""Route handlers.

Phase A shipped two reference handlers; Phase B adds the async
backtest pair so the stdlib webui reaches REQ_F_WEB_003 +
REQ_F_WEB_009 parity:

- ``live_state.build_live_state_handler`` — REQ_F_WEB_002 read.
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
from trading_system.webui.routes.live_state import (
    LiveStateReader,
    build_live_state_handler,
)
from trading_system.webui.routes.registry_promotion import (
    PromotionAuditNotifier,
    RegistryPromoter,
    build_promotion_handler,
)

__all__ = [
    "JobIdGenerator",
    "LiveStateReader",
    "PromotionAuditNotifier",
    "RegistryPromoter",
    "build_live_state_handler",
    "build_promotion_handler",
    "build_status_handler",
    "build_submit_handler",
]
