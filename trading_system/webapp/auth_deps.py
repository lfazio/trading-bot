"""FastAPI dependency-injection wrappers around ``AccountScopedTokenVerifier``.

REQ refs:
- REQ_F_FAS_005 — both ``Authorization: Bearer`` (for tooling) AND
  HTTP-only ``trading-session`` cookies (for the HTMX browser path)
  are accepted. Cookies set by ``POST /api/session`` carry the
  exact operator token (so the verifier path is unchanged) and are
  marked ``HttpOnly`` + ``Secure`` + ``SameSite=Strict``.
- REQ_SDD_FAS_004 — raw operator tokens are never persisted server-
  side; the cookie payload IS the operator token (encrypted in
  flight by TLS; not readable by JS thanks to ``HttpOnly``).
- REQ_NF_FAS_001 / REQ_F_WEB_005 — invariants preserved under FastAPI.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId


_BEARER_PREFIX = "Bearer "

SESSION_COOKIE_NAME = "trading-session"
"""Cookie name the browser uses to carry the operator token.

``POST /api/session`` sets it; ``DELETE /api/session`` clears it.
``GET`` requests that hit a household-gated endpoint accept either
this cookie OR the ``Authorization: Bearer`` header.
"""


def _verifier_from_app(request: Request) -> AccountScopedTokenVerifier:
    """Pull the operator-token verifier off the FastAPI app's state.

    The factory in ``app.py`` attaches one instance at startup so the
    DI graph stays free of module-level globals.
    """
    verifier = getattr(request.app.state, "token_verifier", None)
    if not isinstance(verifier, AccountScopedTokenVerifier):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:token_verifier_missing",
        )
    return verifier


def _extract_token(request: Request) -> str | None:
    """Pull the operator token from one of three sources, in order:

    1. ``Authorization: Bearer <token>`` header — for curl + tooling.
    2. ``trading-session`` cookie — for the HTMX browser path
       (set by ``POST /api/session``).
    3. ``X-Operator-Token`` header — legacy operator tooling.
    """
    headers = request.headers
    auth = headers.get("authorization")
    if auth:
        if auth.startswith(_BEARER_PREFIX):
            return auth[len(_BEARER_PREFIX) :].strip() or None
        return auth.strip() or None
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token.strip() or None
    legacy = headers.get("x-operator-token")
    if legacy:
        return legacy.strip() or None
    return None


def verify_any_valid_claim(
    verifier: AccountScopedTokenVerifier, token: str
) -> bool:
    """Return True if ``token`` verifies under WHATEVER claim it
    carries (HMAC signature + TTL match).

    Use this for browser VIEW endpoints (dashboard, jobs list, job
    detail) where the operator's mental model is "a valid token of
    any scope authorises me to look". Mutation endpoints (registry
    promotion) still require ``require_account_token(account_id)``
    so per-account scoping holds.

    The token format is ``<timestamp>:<account_id>:<signature>``; we
    parse the embedded claim and call ``verifier.verify`` with it.
    A tampered claim string makes the HMAC check fail, so this is
    safe — the operator cannot forge a token whose claim doesn't
    match its signature.
    """
    # ISO timestamps contain ``:`` so rsplit from the right to
    # isolate the two known-fixed segments (account_id + signature).
    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        return False
    _timestamp, claimed_account_id, _signature = parts
    if not claimed_account_id:
        return False
    return verifier.verify(token, account_id=claimed_account_id)


def require_household(request: Request) -> Request:
    """Verify the operator token carries the household claim.

    Read endpoints SHALL depend on this. Returns the request unchanged
    on success; raises ``HTTPException(401)`` with the registry's
    closed Err category (``registry:token_invalid``) on failure so the
    FastAPI body shape matches the stdlib path.
    """
    verifier = _verifier_from_app(request)
    token = _extract_token(request)
    if token is None or not verifier.verify(token, account_id=HOUSEHOLD_CLAIM):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="registry:token_invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return request


RequestRequireHousehold = Annotated[Request, Depends(require_household)]


def require_any_valid_claim(request: Request) -> Request:
    """Verify the operator token under WHATEVER claim it carries
    (household OR any per-account id). Use this for browser
    read-style endpoints (dashboard polling, jobs list, SSE
    streams) so a user signed in with ``default`` claim can still
    VIEW the dashboard — the per-account scope only restricts
    mutations.

    Returns the request on success; raises ``HTTPException(401)``
    with ``registry:token_invalid`` on failure so the FastAPI
    error body matches the stdlib path."""
    verifier = _verifier_from_app(request)
    token = _extract_token(request)
    if token is None or not verify_any_valid_claim(verifier, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="registry:token_invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return request


RequestRequireAnyValidClaim = Annotated[Request, Depends(require_any_valid_claim)]


def require_account_token(account_id: AccountId, request: Request) -> AccountId:
    """Verify the operator token's claim matches ``account_id``.

    Mutation endpoints SHALL gate through this. The account_id
    argument comes from the route's path parameter; pulling it through
    the dependency keeps the signature explicit at every call site.
    """
    verifier = _verifier_from_app(request)
    token = _extract_token(request)
    if token is None or not verifier.verify(token, account_id=str(account_id)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="registry:token_invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return account_id
