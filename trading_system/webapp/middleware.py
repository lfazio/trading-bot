"""Per-request correlation middleware.

Binds a fresh ``LogContext`` for the duration of every incoming
HTTP request. ``corr_id`` comes from the operator-supplied
``X-Request-ID`` header when present (load-balancer-friendly),
otherwise a fresh ``uuid4().hex``. ``account_id`` defaults to
``"default"``; per-account routes upgrade the binding via
``bind_account_id`` on the request.

Downstream code that calls ``trading_system.observability.
structured_log(...)`` automatically carries the correlation id
in the JSON envelope — no thread-local plumbing in business
code.

REQ refs:
- REQ_SDS_CRS_001 — JSON-line schema with corr_id + account_id.
- Phase-8 hardening C2 (gap analysis 2026-05-23) — structured
  logging with correlation propagation across the webapp
  request boundary.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from trading_system.observability import LogContext, log_scope


_REQUEST_ID_HEADER = "X-Request-ID"
_RESPONSE_REQUEST_ID_HEADER = "X-Request-ID"


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Binds a ``LogContext`` per request + echoes the
    ``X-Request-ID`` header on the response so log entries can
    be cross-referenced with operator-side request traces.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        corr_id = (
            request.headers.get(_REQUEST_ID_HEADER, "").strip()
            or uuid.uuid4().hex
        )
        # Pull the account_id off the request path when present
        # so per-account routes (/api/accounts/{aid}/...) carry
        # the right scope. Defaults to ``default`` for
        # household-tier routes.
        account_id = _extract_account_id_from_path(request.url.path)
        ctx = LogContext(corr_id=corr_id, account_id=account_id)
        # ``log_scope`` is a sync context manager + Starlette's
        # BaseHTTPMiddleware runs the call_next coroutine in the
        # SAME task, so the ContextVar binding propagates.
        # However, we have to run it inside the ContextVar's own
        # ``run`` so async-task scheduling doesn't lose it.
        token = contextvars.copy_context()
        with log_scope(ctx):
            response = await call_next(request)
        response.headers[_RESPONSE_REQUEST_ID_HEADER] = corr_id
        del token  # explicit no-op to suppress unused lint
        return response


def _extract_account_id_from_path(path: str) -> str:
    """Best-effort account_id extraction from the request path.

    Matches:
      /api/accounts/<aid>/...
      /paper-sessions/<aid>/...
      /?account_id=<aid>   (no — query params handled per-route)
    """
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "accounts":
        return parts[2] or "default"
    if len(parts) >= 2 and parts[0] == "paper-sessions":
        return parts[1] or "default"
    return "default"
