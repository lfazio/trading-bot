"""Route handlers.

Phase A ships two reference handlers:
- ``live_state.build_live_state_handler`` — REQ_F_WEB_002 read.
- ``registry_promotion.build_promotion_handler`` — REQ_F_WEB_004
  mutation with idempotency + auth + Phase-B-ready AnomalyAlert
  notification hook.

Each builder takes the dependencies it needs and returns a closure
satisfying the ``server.Handler`` signature
(``Request -> JsonResponse``). Phase B adds the remaining endpoints
under the same pattern.
"""

from __future__ import annotations

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
    "LiveStateReader",
    "PromotionAuditNotifier",
    "RegistryPromoter",
    "build_live_state_handler",
    "build_promotion_handler",
]
