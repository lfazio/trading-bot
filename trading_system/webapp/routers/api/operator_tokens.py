"""CR-024 §7 — operator-token lifecycle endpoints.

Two routes:

  POST /api/operator/rotate-secret
    Household-token-gated. Server generates a fresh random
    secret + atomically rotates the token verifier + returns
    the new secret in the response body ONCE for the operator
    to capture (the only safe rotation flow — submitting a
    secret via the request body would leak through proxy logs
    + browser history).

  POST /api/operator/tokens/{jti}/revoke
    Per-account-token-gated. Calls
    OperatorTokenRevocationRepository.revoke + emits the
    standard LogCategory.SECURITY audit.

Both routes are operator-only (household OR per-account
depending on the action). Every authorised action emits a
SECURITY structured-log line carrying event / account_id /
outcome / token_hash per REQ_NF_TOK_001; the raw token NEVER
appears in any log.

REQ refs:
- REQ_F_TOK_003 — multi-secret rolling rotation.
- REQ_F_TOK_002 — TokenRevocationList persistence; revocation
  precedes TTL check.
- REQ_NF_TOK_001 — SECURITY audit on every lifecycle event.
- REQ_F_ACC_010 — operator-token scoping discipline.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Request, status
from starlette.responses import Response

from trading_system.accounts.token_verifier import HOUSEHOLD_CLAIM
from trading_system.models.identifiers import AccountId
from trading_system.observability import structured_log
from trading_system.webapp.canonical import (
    canonical_error_response,
    canonical_json_response,
)


_AUDIT_LOGGER = logging.getLogger(__name__)


router = APIRouter()


# ---------------------------------------------------------------------------
# Protocol slots — the routes consult these via app.state
# ---------------------------------------------------------------------------


@runtime_checkable
class _TokenVerifierView(Protocol):
    """Subset of ``AccountScopedTokenVerifier`` the routes need."""

    def verify(self, token: str, *, account_id: str | None = None) -> bool: ...

    def rotate_secret(self, new_secret: bytes) -> None: ...


@runtime_checkable
class _OperatorTokenRevocationRepoView(Protocol):
    """Subset of ``OperatorTokenRevocationRepository`` the route
    needs."""

    def revoke(
        self, account_id: AccountId, jti: str, reason: str
    ): ...

    def list_for(self, account_id: AccountId): ...


# ---------------------------------------------------------------------------
# Auth helpers (mirror api/live_mode.py + api/hypotheses.py)
# ---------------------------------------------------------------------------


_BEARER_PREFIX = "Bearer "


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX):].strip() or None
    if auth:
        return auth.strip() or None
    legacy = request.headers.get("x-operator-token", "").strip()
    return legacy or None


def _verifier(request: Request) -> _TokenVerifierView:
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:token_verifier_missing",
        )
    return verifier


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_household_token(
    request: Request,
) -> tuple[str, str] | Response:
    """Rotation is a HOUSEHOLD operation — secret rotation
    affects every account_id at once, so it requires the
    household scope (REQ_F_ACC_010 discipline). Per-account
    tokens SHALL be rejected with `registry:household_required`."""
    bearer = _extract_bearer(request)
    if bearer is None:
        return canonical_error_response(
            "registry:token_invalid",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    verifier = _verifier(request)
    if not verifier.verify(bearer, account_id=HOUSEHOLD_CLAIM):
        return canonical_error_response(
            "registry:household_required",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return bearer, _token_hash(bearer)


def _check_account_token(
    request: Request, *, account_id: AccountId
) -> tuple[str, str] | Response:
    """Revocation is per-account: the operator may only revoke
    tokens scoped to an account they hold a per-account token
    for. Household claim REJECTED — household-scope is read-only
    today + revocation is a write."""
    bearer = _extract_bearer(request)
    if bearer is None:
        return canonical_error_response(
            "registry:token_invalid",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    verifier = _verifier(request)
    if verifier.verify(bearer, account_id=HOUSEHOLD_CLAIM):
        return canonical_error_response(
            "registry:household_claim_rejected",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not verifier.verify(bearer, account_id=str(account_id)):
        return canonical_error_response(
            "registry:token_invalid",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return bearer, _token_hash(bearer)


def _audit(
    *,
    event: str,
    account_id: str,
    outcome: str,
    token_hash: str,
    jti: str | None = None,
    message: str = "",
) -> None:
    """REQ_NF_TOK_001 — emit one SECURITY structured-log entry
    per authorised operator-token lifecycle action. The raw
    token NEVER appears."""
    level = logging.INFO if outcome == "ok" else logging.WARNING
    payload: dict[str, object] = {
        "event": event,
        "account_id": account_id,
        "outcome": outcome,
        "token_hash": token_hash,
    }
    if jti is not None:
        payload["jti"] = jti
    if message:
        payload["message"] = message
    structured_log(
        _AUDIT_LOGGER,
        level,
        "security",
        f"operator_token:{event}",
        **payload,
    )


# ---------------------------------------------------------------------------
# Route: rotate the operator secret
# ---------------------------------------------------------------------------


@router.post(
    "/api/operator/rotate-secret",
    response_class=Response,
    summary="Rotate the operator-token secret (CR-024 / REQ_F_TOK_003)",
)
def post_rotate_secret(request: Request) -> Response:
    """Generate a fresh 64-byte random secret + atomically rotate
    the verifier + return the new secret in the response body
    ONCE.

    The operator captures the new secret server-side over HTTPS
    and stores it in their secrets manager. The previous secret
    enters the grace window — existing tokens signed with it
    keep verifying until the NEXT rotation drops it
    (REQ_F_TOK_003 multi-secret rolling rotation).
    """
    auth = _check_household_token(request)
    if isinstance(auth, Response):
        return auth
    _token, token_hash = auth

    verifier = _verifier(request)
    new_secret_bytes = secrets.token_bytes(64)
    try:
        verifier.rotate_secret(new_secret_bytes)
    except Exception as e:  # noqa: BLE001 — defensive
        _audit(
            event="rotate_secret",
            account_id="",
            outcome="failed",
            token_hash=token_hash,
            message=f"{type(e).__name__}: {e}",
        )
        return canonical_error_response(
            f"registry:rotate_secret_failed:{e}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    _audit(
        event="rotate_secret",
        account_id="",
        outcome="ok",
        token_hash=token_hash,
    )
    return canonical_json_response(
        {
            "new_secret_hex": new_secret_bytes.hex(),
            "rotated_at": datetime.now(tz=UTC).isoformat(),
            "grace_window": "previous secret accepted until next rotation",
        }
    )


# ---------------------------------------------------------------------------
# Route: revoke an operator token by jti
# ---------------------------------------------------------------------------


@router.post(
    "/api/operator/accounts/{account_id}/tokens/{jti}/revoke",
    response_class=Response,
    summary="Revoke an operator token by jti (CR-024 / REQ_F_TOK_002)",
)
def post_revoke_token(
    account_id: AccountId,
    jti: str,
    request: Request,
) -> Response:
    """Add ``(account_id, jti)`` to the revocation list. After
    this call the token can no longer authenticate any
    operation; the revocation check precedes TTL verification.

    REQ_F_TOK_002: the jti pair is the canonical revocation key.
    Legacy 3-segment tokens (no jti) are unrevocable through this
    endpoint; the operator rotates the secret instead.
    """
    if not jti.strip():
        return canonical_error_response(
            "webapp:missing_path_param:jti",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    auth = _check_account_token(request, account_id=account_id)
    if isinstance(auth, Response):
        return auth
    _token, token_hash = auth

    repo = getattr(request.app.state, "operator_token_revocation_repo", None)
    if repo is None:
        return canonical_error_response(
            "webapp:operator_token_revocation_repo_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    try:
        result = repo.revoke(
            account_id=account_id,
            jti=jti,
            reason="operator-initiated",
        )
    except Exception as e:  # noqa: BLE001 — defensive
        _audit(
            event="revoke_token",
            account_id=str(account_id),
            outcome="failed",
            token_hash=token_hash,
            jti=jti,
            message=f"{type(e).__name__}: {e}",
        )
        return canonical_error_response(
            f"registry:revoke_failed:{e}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if hasattr(result, "error"):
        _audit(
            event="revoke_token",
            account_id=str(account_id),
            outcome="failed",
            token_hash=token_hash,
            jti=jti,
            message=result.error,
        )
        return canonical_error_response(
            result.error,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    _audit(
        event="revoke_token",
        account_id=str(account_id),
        outcome="ok",
        token_hash=token_hash,
        jti=jti,
    )
    return canonical_json_response(
        {"account_id": str(account_id), "jti": jti, "revoked": True}
    )


# ---------------------------------------------------------------------------
# Route: list active operator tokens for an account
# ---------------------------------------------------------------------------


@router.get(
    "/api/operator/accounts/{account_id}/tokens/revoked",
    response_class=Response,
    summary="List revoked operator tokens for an account (CR-024)",
)
def get_revoked_tokens(
    account_id: AccountId,
    request: Request,
) -> Response:
    """Per-account-token-gated; household claim REJECTED.

    Returns the canonical-JSON list of revoked
    `(account_id, jti, reason, revoked_at)` rows so the
    operator UI can render the table beneath an "active vs
    revoked" toggle. v1 lists revoked entries only — issuing
    a token doesn't go through a central registry (operators
    issue via the `trading-bot issue-token` CLI per
    REQ_F_TOK_005) so the webapp can't enumerate ACTIVE
    tokens. Operators track issued jti's externally
    (deployment secret store) + revoke by jti via the POST
    endpoint above.
    """
    auth = _check_account_token(request, account_id=account_id)
    if isinstance(auth, Response):
        return auth

    repo = getattr(request.app.state, "operator_token_revocation_repo", None)
    if repo is None:
        return canonical_error_response(
            "webapp:operator_token_revocation_repo_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    try:
        rows = repo.list_all(account_id=account_id)
    except Exception as e:  # noqa: BLE001
        return canonical_error_response(
            f"registry:list_revocations_failed:{e}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if hasattr(rows, "error"):
        return canonical_error_response(
            rows.error,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if hasattr(rows, "value"):
        rows = rows.value

    return canonical_json_response(
        {
            "account_id": str(account_id),
            "revoked": [
                {
                    "jti": str(getattr(r, "jti", "")),
                    "reason": str(getattr(r, "reason", "")),
                    "revoked_at": (
                        getattr(r, "revoked_at", datetime.now(tz=UTC))
                    ).isoformat()
                    if hasattr(r, "revoked_at")
                    else "",
                }
                for r in rows
            ],
        }
    )
