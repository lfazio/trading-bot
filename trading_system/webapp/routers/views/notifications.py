"""Notifications inbox view (REQ_F_WEB2_009)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim
from trading_system.webapp.fragments import fragment_context
from trading_system.webapp.inbox import InboxChannel


router = APIRouter()


@router.get("/notifications", response_class=HTMLResponse, name="notifications")
def get_notifications(request: Request):
    """Render the inbox panel. Auth: any valid claim."""
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verify_any_valid_claim(verifier, token)
    ):
        return RedirectResponse(url="/login", status_code=303)

    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise RuntimeError("webapp:templates_missing")

    channel = getattr(request.app.state, "notification_inbox", None)
    entries = ()
    if isinstance(channel, InboxChannel):
        # Display newest first.
        entries = tuple(reversed(channel.snapshot()))
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={"entries": entries, **fragment_context(request)},
    )
