"""CR-019 step 2 — Live-mode FastAPI write routes.

Four routes per REQ_F_LIV_008 / REQ_SDD_LIV_005:

  POST /api/accounts/{account_id}/live-mode/enable
  POST /api/accounts/{account_id}/live-mode/disable
  POST /api/accounts/{account_id}/emergency-stop
  POST /api/accounts/{account_id}/broker-reconnect

All four:
- Gated by the per-account-scoped operator token. The household
  claim (``HOUSEHOLD_CLAIM``) is REJECTED — the route SHALL be
  per-account only.
- Emit a ``LogCategory.SECURITY`` structured-log entry on every
  authorised action carrying ``event`` / ``account_id`` /
  ``outcome`` / ``token_hash`` (REQ_NF_TOK_001).

The router stays plumbing-only: domain semantics (live-mode flip,
emergency stop, broker reconnection) delegate to small Protocol
slots on ``app.state`` so the routes can be tested without the
full live runtime.

REQ refs: REQ_F_LIV_002, REQ_F_LIV_003, REQ_F_LIV_006, REQ_F_LIV_008,
REQ_SDD_LIV_005, REQ_F_ACC_010, REQ_NF_TOK_001.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Request, status
from starlette.responses import Response

from trading_system.accounts.token_verifier import HOUSEHOLD_CLAIM
from trading_system.models.identifiers import AccountId
from trading_system.observability import structured_log
from trading_system.result import Err, Ok
from trading_system.webapp.canonical import (
    canonical_error_response,
    canonical_json_response,
)


_AUDIT_LOGGER = logging.getLogger(__name__)


router = APIRouter(prefix="/api/accounts")


# ---------------------------------------------------------------------------
# Operator-action Protocol slots — small surfaces the runtime wires in
# ---------------------------------------------------------------------------


@runtime_checkable
class LiveModeController(Protocol):
    """REQ_F_LIV_002 / REQ_F_LIV_003 — the live-mode toggle.

    Concrete v1 implementation: a small object holding a reference
    to the ``LiveRuntimeRegistry`` + the operator's preflight
    artefact path. ``enable`` re-runs the preflight inline; on Ok
    it flips a per-account ``mode_enabled`` flag the dashboard
    reads. ``disable`` stops any active live runtime via the
    registry's existing ``stop`` method.
    """

    def enable(self, account_id: AccountId) -> object: ...

    def disable(self, account_id: AccountId) -> object: ...


@runtime_checkable
class EmergencyStopController(Protocol):
    """REQ_F_LIV_003 / REQ_F_LIV_006 — emergency-stop control.

    Concrete v1: wraps ``SafetyLayer.escalate_to_kill(account_id)``
    so the dashboard's emergency-stop button flips the per-account
    KS to KILL via the existing safety surface (no broker-specific
    escalation path).
    """

    def trigger(self, account_id: AccountId) -> object: ...


@runtime_checkable
class BrokerReconnectController(Protocol):
    """REQ_F_LIV_006 case (b) — broker re-authentication.

    Concrete v1: re-runs the broker authentication gate from the
    preflight. Returns Ok on success; Err with a categorised
    string on failure.
    """

    def reconnect(self, account_id: AccountId) -> object: ...


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


_BEARER_PREFIX = "Bearer "


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX) :].strip() or None
    if auth:
        return auth.strip() or None
    legacy = request.headers.get("x-operator-token", "").strip()
    return legacy or None


def _verifier(request: Request):
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:token_verifier_missing",
        )
    return verifier


def _live_mode_controller(request: Request) -> LiveModeController | None:
    return getattr(request.app.state, "live_mode_controller", None)


def _emergency_stop_controller(
    request: Request,
) -> EmergencyStopController | None:
    return getattr(request.app.state, "emergency_stop_controller", None)


def _broker_reconnect_controller(
    request: Request,
) -> BrokerReconnectController | None:
    return getattr(request.app.state, "broker_reconnect_controller", None)


def _token_hash(token: str) -> str:
    """SHA-256 of the raw token — the audit row identifier.

    REQ_SDD_PER_005 discipline: the raw token is NEVER persisted,
    only its hash. Cross-references the security-log entries with
    persisted promotion-audit rows.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_account_token(
    request: Request, *, account_id: AccountId
) -> tuple[str, str] | Response:
    """Returns ``(token, token_hash)`` on success or a
    ``canonical_error_response`` on auth failure.

    REQ_F_LIV_008 — the household claim is REJECTED on all four
    live-mode write routes; the token MUST carry the targeted
    ``account_id`` claim.
    """
    bearer = _extract_bearer(request)
    if bearer is None:
        return canonical_error_response(
            "registry:token_invalid",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    verifier = _verifier(request)
    # Household claim explicitly rejected (REQ_F_LIV_008).
    if verifier.verify(bearer, account_id=HOUSEHOLD_CLAIM):
        return canonical_error_response(
            "live:household_claim_rejected",
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
    account_id: AccountId,
    outcome: str,
    token_hash: str,
    message: str = "",
) -> None:
    """REQ_NF_TOK_001 — every authorised live-mode action emits a
    SECURITY structured-log entry. The raw token SHALL NOT appear
    in the payload — only ``token_hash``.
    """
    level = logging.INFO if outcome == "ok" else logging.WARNING
    payload: dict[str, object] = {
        "event": event,
        "account_id": str(account_id),
        "outcome": outcome,
        "token_hash": token_hash,
    }
    if message:
        payload["message"] = message
    structured_log(
        _AUDIT_LOGGER, level, "security", f"live_mode:{event}", **payload
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/{account_id}/live-mode/enable",
    response_class=Response,
    summary="Enable live-trading mode for the account",
)
def post_enable(account_id: AccountId, request: Request) -> Response:
    """REQ_F_LIV_002 — flip the dashboard's `live` chip on for the
    targeted account. Re-runs the preflight inline via the
    ``LiveModeController.enable`` Protocol slot."""
    auth = _check_account_token(request, account_id=account_id)
    if isinstance(auth, Response):
        return auth
    _token, token_hash = auth
    controller = _live_mode_controller(request)
    if controller is None:
        return canonical_error_response(
            "webapp:live_mode_controller_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    result = controller.enable(account_id)
    if isinstance(result, Err):
        _audit(
            event="live_mode_enable",
            account_id=account_id,
            outcome="failed",
            token_hash=token_hash,
            message=result.error,
        )
        return canonical_error_response(
            result.error,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    _audit(
        event="live_mode_enable",
        account_id=account_id,
        outcome="ok",
        token_hash=token_hash,
    )
    return canonical_json_response(
        {"account_id": str(account_id), "live_mode": "enabled"},
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/{account_id}/live-mode/disable",
    response_class=Response,
    summary="Disable live-trading mode for the account",
)
def post_disable(account_id: AccountId, request: Request) -> Response:
    """REQ_F_LIV_002 — flip the chip off. Existing in-flight live
    session SHALL be stopped gracefully via the ``LiveRuntimeRegistry``."""
    auth = _check_account_token(request, account_id=account_id)
    if isinstance(auth, Response):
        return auth
    _token, token_hash = auth
    controller = _live_mode_controller(request)
    if controller is None:
        return canonical_error_response(
            "webapp:live_mode_controller_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    result = controller.disable(account_id)
    if isinstance(result, Err):
        _audit(
            event="live_mode_disable",
            account_id=account_id,
            outcome="failed",
            token_hash=token_hash,
            message=result.error,
        )
        return canonical_error_response(
            result.error,
            status_code=status.HTTP_409_CONFLICT,
        )
    _audit(
        event="live_mode_disable",
        account_id=account_id,
        outcome="ok",
        token_hash=token_hash,
    )
    return canonical_json_response(
        {"account_id": str(account_id), "live_mode": "disabled"},
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/{account_id}/emergency-stop",
    response_class=Response,
    summary="Emergency-stop the per-account kill switch",
)
def post_emergency_stop(account_id: AccountId, request: Request) -> Response:
    """REQ_F_LIV_003 / REQ_F_LIV_006 — KILL the per-account kill
    switch via the existing ``safety/state_manager.py`` surface."""
    auth = _check_account_token(request, account_id=account_id)
    if isinstance(auth, Response):
        return auth
    _token, token_hash = auth
    controller = _emergency_stop_controller(request)
    if controller is None:
        return canonical_error_response(
            "webapp:emergency_stop_controller_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    result = controller.trigger(account_id)
    if isinstance(result, Err):
        _audit(
            event="emergency_stop",
            account_id=account_id,
            outcome="failed",
            token_hash=token_hash,
            message=result.error,
        )
        return canonical_error_response(
            result.error,
            status_code=status.HTTP_409_CONFLICT,
        )
    _audit(
        event="emergency_stop",
        account_id=account_id,
        outcome="ok",
        token_hash=token_hash,
    )
    return canonical_json_response(
        {"account_id": str(account_id), "kill_switch": "KILL"},
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/{account_id}/broker-reconnect",
    response_class=Response,
    summary="Re-authenticate the broker adapter",
)
def post_broker_reconnect(
    account_id: AccountId, request: Request
) -> Response:
    """REQ_F_LIV_006 case (b) — re-attempt broker authentication
    after a ``broker:not_authenticated`` error tripped the KS."""
    auth = _check_account_token(request, account_id=account_id)
    if isinstance(auth, Response):
        return auth
    _token, token_hash = auth
    controller = _broker_reconnect_controller(request)
    if controller is None:
        return canonical_error_response(
            "webapp:broker_reconnect_controller_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    result = controller.reconnect(account_id)
    if isinstance(result, Err):
        _audit(
            event="broker_reconnect",
            account_id=account_id,
            outcome="failed",
            token_hash=token_hash,
            message=result.error,
        )
        return canonical_error_response(
            result.error,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    _audit(
        event="broker_reconnect",
        account_id=account_id,
        outcome="ok",
        token_hash=token_hash,
    )
    return canonical_json_response(
        {"account_id": str(account_id), "broker": "reconnected"},
        status_code=status.HTTP_200_OK,
    )
