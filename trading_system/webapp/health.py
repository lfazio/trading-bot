"""``GET /health`` — endpoint the container HEALTHCHECK polls.

REQ refs: REQ_F_FAS_007 — the Dockerfile's HEALTHCHECK directive
hits this endpoint; a 200 response = healthy.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter
from starlette.responses import Response

from trading_system.webapp.canonical import canonical_json_response


router = APIRouter()


def _version() -> str:
    """Best-effort version string for ``/health``.

    Prefers ``TRADING_BOT_VERSION`` (CI injects the git SHA) +
    falls back to ``"0.0.0-dev"`` for local runs.
    """
    return os.environ.get("TRADING_BOT_VERSION", "0.0.0-dev")


@router.get("/health", response_class=Response)
def get_health() -> Response:
    """Return ``{"status": "ok", "as_of": <iso8601>, "version": ...}``
    so the container's HEALTHCHECK can poll a stable endpoint without
    authentication."""
    return canonical_json_response(
        {
            "status": "ok",
            "as_of": datetime.now(UTC).isoformat(),
            "version": _version(),
        }
    )
