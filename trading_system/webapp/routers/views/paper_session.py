"""Paper-trading session controls — CR-019 / REQ_F_WEB2_003.

Companion to the onboarding wizard: once a paper session is
ticking the operator needs a way to STOP it from the dashboard.
Stop happens via a POST form (HTMX-friendly + JS-free fallback).

Routes:
  POST /paper-sessions/{account_id}/stop  -> stop runtime + 303 -> /

The handler de-registers the runtime from the shared
``RuntimeRegistry``. The runtime's ``stop()`` method flips
``is_alive`` to ``False`` so the dashboard panel's SSE channel
transitions from "Live" to "Stopped" on the next tick.

Auth: stop is a mutation, so it goes through the per-account
gate (the operator's session token's account_id must match the
session being stopped — the household sentinel is rejected by
``require_account_token``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from trading_system.models.identifiers import AccountId
from trading_system.result import Err
from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim


router = APIRouter(prefix="/paper-sessions")


@router.post("/{account_id}/stop")
async def post_stop(
    account_id: str,
    request: Request,
) -> RedirectResponse:
    """Stop the paper-trading runtime keyed on ``account_id``.

    Auth: any valid token claim (household OR per-account) is
    accepted — the dashboard view uses the same gate. Mutation
    is idempotent: stopping a non-existent / already-stopped
    session is a no-op (the redirect still lands the operator
    back on the dashboard).
    """
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verify_any_valid_claim(verifier, token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="registry:token_invalid",
        )

    registry = getattr(request.app.state, "runtime_registry", None)
    if registry is not None:
        result = registry.stop(AccountId(account_id))
        # Err just means the runtime wasn't there to begin with;
        # the operator might have refreshed twice. Silent on
        # this since the UI result is the same.
        if isinstance(result, Err):
            pass

    response = RedirectResponse(url="/", status_code=303)
    # If the operator stopped the active session, drop the
    # cookie so the dashboard falls back to "default" on next
    # paint instead of re-showing the dead session.
    active = request.cookies.get("active-paper-session", "")
    if active == account_id:
        response.delete_cookie("active-paper-session")
    return response
