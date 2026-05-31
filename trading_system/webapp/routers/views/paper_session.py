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
    runtime_was_live = False
    if registry is not None:
        result = registry.stop(AccountId(account_id))
        # Err just means the runtime wasn't there to begin with;
        # the operator might have refreshed twice. Silent on
        # this since the UI result is the same.
        runtime_was_live = not isinstance(result, Err)

    # Log the session-stop into the inbox if wired (only when
    # we actually stopped a registered session — refreshes don't
    # spam the log).
    inbox = getattr(request.app.state, "notification_inbox", None)
    if runtime_was_live and inbox is not None and hasattr(inbox, "append"):
        from datetime import UTC, datetime

        from trading_system.webapp.inbox import InboxEntry

        try:
            inbox.append(
                InboxEntry(
                    at=datetime.now(tz=UTC),
                    category="paper-session",
                    code="session_stopped",
                    severity="info",
                    message="Paper session stopped by operator.",
                    account_id=account_id,
                )
            )
        except Exception:  # noqa: BLE001 — inbox failures stay non-fatal
            pass

    response = RedirectResponse(url="/", status_code=303)
    # If the operator stopped the active session, drop the
    # cookie so the dashboard falls back to "default" on next
    # paint instead of re-showing the dead session.
    active = request.cookies.get("active-paper-session", "")
    if active == account_id:
        response.delete_cookie("active-paper-session")
    return response


@router.post("/{account_id}/rehydrate")
async def post_rehydrate(
    account_id: str,
    request: Request,
) -> RedirectResponse:
    """CR-019 §6 — one-click resume after a webapp restart.

    Reads the wizard's persisted metadata (via the Protocol-shaped
    ``paper_session_repository`` slot) + rebuilds the runtime via
    the same factory the wizard's finish handler uses + registers
    it. Idempotent on already-running sessions (caller gets
    redirected back to the dashboard with the categorised flash
    cookie).

    Auth: same gate as ``post_stop`` (any valid claim).
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

    paper_session_repo = getattr(
        request.app.state, "paper_session_repository", None
    )
    registry = getattr(request.app.state, "runtime_registry", None)
    if paper_session_repo is None or registry is None:
        # Persistence or registry not wired — the operator can't
        # rehydrate from this deploy. Redirect with a flash so the
        # dashboard surfaces the categorised reason.
        return _flash_redirect(
            "/", "paper:rehydrate:not_configured"
        )

    from trading_system.webapp.runtimes.runtime_rehydrator import (
        RehydrateRequest,
        rehydrate_paper_session,
    )

    rehydrate_result = rehydrate_paper_session(
        RehydrateRequest(
            account_id=AccountId(account_id),
            paper_session_repo=paper_session_repo,
            runtime_registry=registry,
            instrument_bar_repo=getattr(
                request.app.state, "instrument_bar_repository", None
            ),
        )
    )

    inbox = getattr(request.app.state, "notification_inbox", None)
    if isinstance(rehydrate_result, Err):
        if inbox is not None and hasattr(inbox, "append"):
            from datetime import UTC, datetime

            from trading_system.webapp.inbox import InboxEntry

            try:
                inbox.append(
                    InboxEntry(
                        at=datetime.now(tz=UTC),
                        category="paper-session",
                        code="rehydrate_failed",
                        severity="warning",
                        message=(
                            f"Paper-session rehydration failed: "
                            f"{rehydrate_result.error}"
                        ),
                        account_id=account_id,
                    )
                )
            except Exception:  # noqa: BLE001
                pass
        return _flash_redirect("/", rehydrate_result.error)

    # Success — surface the operator-visible flash + set the
    # active-paper-session cookie so the dashboard's panel reads
    # the resumed runtime instead of "default".
    if inbox is not None and hasattr(inbox, "append"):
        from datetime import UTC, datetime

        from trading_system.webapp.inbox import InboxEntry

        try:
            inbox.append(
                InboxEntry(
                    at=datetime.now(tz=UTC),
                    category="paper-session",
                    code="rehydrated",
                    severity="info",
                    message="Paper session rehydrated by operator.",
                    account_id=account_id,
                )
            )
        except Exception:  # noqa: BLE001
            pass
    response = _flash_redirect(
        f"/?account_id={account_id}",
        f"paper:rehydrated:{account_id}",
    )
    response.set_cookie(
        "active-paper-session",
        account_id,
        max_age=3600,
        samesite="lax",
    )
    return response


def _flash_redirect(location: str, flash: str) -> RedirectResponse:
    """One-shot flash cookie the dashboard reads on next paint
    + then deletes. Matches the existing `strategies-flash`
    pattern from the registry-promotion view."""
    response = RedirectResponse(url=location, status_code=303)
    response.set_cookie(
        "paper-rehydrate-flash",
        flash,
        max_age=60,
        samesite="lax",
    )
    return response
