"""HTMX dashboard page — Phase A scope.

REQ refs:
- REQ_F_FAS_002 — server-rendered HTML with HTMX interactivity; no
  client-side JS bundle beyond ``htmx.min.js``.

The dashboard renders ``GET /`` and hydrates the live-state block via
HTMX `hx-get` on a poll trigger (SSE auto-refresh moves in Phase B
once `sse-starlette` wiring lands).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse

from trading_system.webapp.auth_deps import RequestRequireHousehold


router = APIRouter()


def _templates(request: Request) -> Jinja2Templates:
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        # The factory in app.py SHALL attach this; the structural test
        # verifies. We surface a 500 with a stable code so an
        # operator can diagnose a mis-wired deployment.
        raise RuntimeError("webapp:templates_missing")
    return templates


@router.get("/", response_class=HTMLResponse, name="dashboard")
def get_dashboard(request: RequestRequireHousehold) -> HTMLResponse:
    """Render the Phase-A dashboard.

    Authenticated callers (household token) see the chrome plus an
    HTMX `hx-get` block that polls `/api/accounts/default/live-state`
    every 5 s. Phase B swaps the poll for an SSE channel.
    """
    return _templates(request).TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"account_id": "default"},
    )
