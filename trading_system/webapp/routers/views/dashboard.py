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

from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim


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

    Auth: any valid token claim (household OR per-account) is
    accepted for the VIEW. Mutation endpoints (registry promotion)
    keep per-account scoping via ``require_account_token``.
    """
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verify_any_valid_claim(verifier, token)
    ):
        return RedirectResponse(url="/login", status_code=303)
    # Choose which session the panel SSE-connects to, in order:
    # 1. ``?account_id=<id>`` query param (just-finished wizard
    #    redirect, or a manual switch from the multi-account
    #    selector).
    # 2. ``active-paper-session`` cookie (last finished wizard,
    #    1h lifetime) — so the operator refreshing ``/`` or
    #    coming back later still sees their session.
    # 3. ``"default"`` household claim — pre-onboarding state.
    account_id = (
        request.query_params.get("account_id", "").strip()
        or request.cookies.get("active-paper-session", "").strip()
        or "default"
    )
    # Surface every currently-live paper session in a switcher
    # so the operator can hop between them without retyping the
    # query string. Defensive against an unwired registry.
    registry = getattr(request.app.state, "runtime_registry", None)
    live_paper_sessions: tuple[str, ...] = ()
    if registry is not None and hasattr(registry, "live_account_ids"):
        try:
            live_paper_sessions = tuple(str(a) for a in registry.live_account_ids())
        except Exception:  # noqa: BLE001 — defensive
            live_paper_sessions = ()
    return _templates(request).TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "account_id": account_id,
            "live_paper_sessions": live_paper_sessions,
        },
    )
