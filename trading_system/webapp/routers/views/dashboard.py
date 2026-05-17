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
from starlette.responses import HTMLResponse, RedirectResponse

from trading_system.accounts.token_verifier import HOUSEHOLD_CLAIM
from trading_system.webapp.auth_deps import _extract_token


router = APIRouter()


def _templates(request: Request) -> Jinja2Templates:
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise RuntimeError("webapp:templates_missing")
    return templates


@router.get("/", response_class=HTMLResponse, name="dashboard")
def get_dashboard(request: Request):
    """Render the Phase-B dashboard.

    Browser path is graceful: a missing or invalid session cookie
    redirects to ``/login`` rather than returning a raw 401 JSON.
    Tooling (curl + httpx) still gets the JSON-shaped 401 from the
    other endpoints; only the HTML entry point is browser-friendly.
    """
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verifier.verify(token, account_id=HOUSEHOLD_CLAIM)
    ):
        return RedirectResponse(url="/login", status_code=303)
    return _templates(request).TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"account_id": "default"},
    )
