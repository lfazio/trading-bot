"""Web interface — CR-004 Phase 6.

Stdlib-only HTTP API for monitoring + registry promotion + async
backtest invocation. Phase A (this slice) ships the auth wrapper,
canonical response envelope, in-memory idempotency store, route
registry, server skeleton, and two reference routes (live state +
registry promotion). Phase B (deferred):
- JobQueue + BacktestJobRepository for async backtest invocation.
- Concrete read endpoints wired into live Portfolio / Analytics /
  Registry types (Phase A uses Protocol-shaped readers).
- Child-process isolation (REQ_NF_WEB_001 kill-the-webui drill).
- ``config/webui.yaml`` 10th YAML loader.
- SPA bundle (HTMX or similar).

REQ refs: REQ_F_WEB_001..010, REQ_NF_WEB_001..002, REQ_SDS_WEB_001..004,
REQ_SDD_WEB_001..008.
"""

from __future__ import annotations

from trading_system.webui.auth import WebAuth
from trading_system.webui.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
)
from trading_system.webui.schemas import (
    JsonResponse,
    LiveStateResponse,
    PromoteResponse,
    canonical_response,
)
from trading_system.webui.server import (
    Request,
    Route,
    Router,
    WebUIServer,
)

__all__ = [
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "JsonResponse",
    "LiveStateResponse",
    "PromoteResponse",
    "Request",
    "Route",
    "Router",
    "WebAuth",
    "WebUIServer",
    "canonical_response",
]
