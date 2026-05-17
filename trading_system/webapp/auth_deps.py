"""FastAPI dependency-injection wrappers around ``AccountScopedTokenVerifier``.

REQ refs:
- REQ_F_FAS_005 — Bearer-token authentication.
- REQ_SDD_FAS_004 — auth dependencies + raw operator tokens never
  persisted (the verifier compares HMACs, never raw strings).
- REQ_NF_FAS_001 / REQ_F_WEB_005 — invariants preserved under FastAPI.

Phase A scope ships **Bearer-token auth only**. The Phase-B cookie
session lands once the operator wires the session-secret config.
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
    """Pull the operator token from ``Authorization: Bearer ...`` or
    the legacy ``X-Operator-Token`` header (case-insensitive lookup)."""
    headers = request.headers
    auth = headers.get("authorization")
    if auth:
        if auth.startswith(_BEARER_PREFIX):
            return auth[len(_BEARER_PREFIX) :].strip() or None
        return auth.strip() or None
    legacy = headers.get("x-operator-token")
    if legacy:
        return legacy.strip() or None
    return None


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
