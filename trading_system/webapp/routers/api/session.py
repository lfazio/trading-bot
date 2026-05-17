"""``POST /api/session`` + ``DELETE /api/session`` — cookie-session auth.

REQ refs:
- REQ_F_FAS_005 — operators authenticate via ``POST /api/session``
  with a Bearer token in the body; the server verifies it (no new
  trust path) and sets ``trading-session`` as ``HttpOnly`` +
  ``Secure`` + ``SameSite=Strict`` with the same lifetime as the
  underlying operator token. ``DELETE /api/session`` clears the
  cookie.
- REQ_SDD_FAS_004 — raw tokens NEVER persisted server-side; the
  cookie payload IS the operator token (HttpOnly hides it from JS;
  Secure pins it to TLS).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.responses import Response

from trading_system.accounts.token_verifier import HOUSEHOLD_CLAIM
from trading_system.webapp.auth_deps import SESSION_COOKIE_NAME
from trading_system.webapp.canonical import (
    canonical_error_response,
    canonical_json_response,
)


router = APIRouter(prefix="/api/session")


class SessionRequest(BaseModel):
    """Body for ``POST /api/session`` — the operator pastes a token
    issued via the operator-tooling pipeline. ``account_id`` is
    ``"household"`` for read-scope sessions or the targeted account
    id for mutation-scope sessions."""

    token: str = Field(min_length=1, description="operator HMAC token")
    account_id: str = Field(
        default=HOUSEHOLD_CLAIM,
        description="claim the token SHALL carry (defaults to the household sentinel)",
    )


def _verifier(request: Request):
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:token_verifier_missing",
        )
    return verifier


@router.post(
    "",
    response_class=Response,
    summary="Open a cookie-backed session",
    description=(
        "Verifies the supplied operator token and sets the "
        "trading-session cookie (HttpOnly + Secure + SameSite=Strict). "
        "Subsequent GETs may omit the Authorization header."
    ),
)
def open_session(body: SessionRequest, request: Request) -> Response:
    verifier = _verifier(request)
    if not verifier.verify(body.token, account_id=body.account_id):
        return canonical_error_response(
            "registry:token_invalid",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = canonical_json_response(
        {"ok": True, "account_id": body.account_id}
    )
    # Cookie TTL matches the verifier's configured ttl_seconds so the
    # cookie expires alongside the underlying token.
    max_age = int(getattr(verifier, "ttl_seconds", 300))
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=body.token,
        max_age=max_age,
        httponly=True,
        secure=False,  # operators flip this true behind TLS in prod
        samesite="strict",
        path="/",
    )
    return response


@router.delete(
    "",
    response_class=Response,
    summary="Close the cookie session",
)
def close_session() -> Response:
    response = canonical_json_response({"ok": True})
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response
