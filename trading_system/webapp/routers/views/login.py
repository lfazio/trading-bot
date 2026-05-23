"""``GET /login`` — HTML login form for the HTMX dashboard.

REQ refs:
- REQ_F_FAS_002 — server-rendered HTMX page.
- REQ_F_FAS_005 — operators paste an issued operator token into the
  form; the form POSTs to ``/api/session`` which sets the
  ``trading-session`` cookie. Subsequent dashboard navigation works
  without an Authorization header.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse

from trading_system.webapp.fragments import fragment_context


router = APIRouter()


def _templates(request: Request):
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise RuntimeError("webapp:templates_missing")
    return templates


@router.get("/login", response_class=HTMLResponse, name="login")
def get_login(request: Request) -> HTMLResponse:
    """Render the login form. No auth — operators paste a token to
    establish a cookie session."""
    return _templates(request).TemplateResponse(
        request=request,
        name="login.html",
        context={**fragment_context(request)},
    )
