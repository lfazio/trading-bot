"""Strategy registry panel view (REQ_F_WEB2_006).

Browse view + HTML-form promote wrapper around the JSON
``/api/registry/{strategy_id}/promote`` endpoint.

Two routes:
- GET  /strategies                       -> list strategies + status
- POST /strategies/{strategy_id}/promote -> form-encoded promote

The view layer never imports the concrete registry; it consumes
Protocol-shaped slots from ``app.state``:
- ``strategy_registry_reader`` — list of strategies + status
- ``registry_promoter`` — promote(strategy_id, ...) (same Protocol
  the JSON API already consumes).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Protocol, runtime_checkable

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.result import Err
from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim
from trading_system.webapp.fragments import fragment_context


router = APIRouter(prefix="/strategies")


@runtime_checkable
class StrategyRegistryReader(Protocol):
    """List-only surface — returns the strategies the registry
    knows about, with their lifecycle status. CR-002 Phase B will
    wire a concrete reader over the SQLite registry."""

    def list_strategies(self) -> list[dict[str, object]]:
        """Each dict carries at least ``id``, ``status``
        (``"experimental"`` / ``"validated"`` / ``"deprecated"``),
        and optionally ``last_promoted_at`` + ``improvement_report``
        (a short string description)."""
        ...


def _require_auth(request: Request) -> bool:
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    return (
        verifier is not None
        and token is not None
        and verify_any_valid_claim(verifier, token)
    )


@router.get("", response_class=HTMLResponse, name="strategies")
def get_strategies(request: Request):
    """Render the strategy registry list."""
    if not _require_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    templates = request.app.state.templates
    reader = getattr(request.app.state, "strategy_registry_reader", None)
    strategies: list[dict[str, object]] = []
    if reader is not None and hasattr(reader, "list_strategies"):
        try:
            strategies = list(reader.list_strategies())
        except Exception:  # noqa: BLE001 — defensive
            strategies = []
    # Pull the flash from the cookie + clear it so the banner shows
    # once after a promote round-trip.
    flash = request.cookies.get("strategies-flash", "")
    response = templates.TemplateResponse(
        request=request,
        name="strategies.html",
        context={
            "strategies": strategies,
            "flash": flash or None,
            "promoter_wired": getattr(
                request.app.state, "registry_promoter", None
            )
            is not None,
            **fragment_context(request),
        },
    )
    if flash:
        response.delete_cookie("strategies-flash")
    return response


@router.post(
    "/{strategy_id}/promote",
    response_class=HTMLResponse,
    name="strategies-promote",
)
def post_promote(
    strategy_id: str,
    request: Request,
    operator_token: Annotated[str, Form()],
    operator_id: Annotated[str, Form()],
    rationale: Annotated[str, Form()],
    account_id: Annotated[str, Form()] = "default",
):
    """HTML form wrapper around the JSON promote endpoint. The
    operator-token cookie holds the bearer auth; the form's
    ``operator_token`` is the action-specific HMAC token the
    promoter verifies."""
    if not _require_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    promoter = getattr(request.app.state, "registry_promoter", None)
    if promoter is None:
        return _redirect_with_flash(
            "/strategies", "webapp:registry_promoter_missing"
        )
    result = promoter.promote(
        strategy_id=StrategyId(strategy_id),
        operator_token=operator_token,
        operator_id=operator_id,
        rationale=rationale,
        account_id=AccountId(account_id),
    )
    if isinstance(result, Err):
        return _redirect_with_flash("/strategies", result.error)
    # Success — fire the audit notifier if wired.
    notifier = getattr(request.app.state, "promotion_audit_notifier", None)
    if notifier is not None and hasattr(notifier, "dispatch"):
        try:
            notifier.dispatch(
                {
                    "event": "registry.promotion",
                    "strategy_id": strategy_id,
                    "operator_id": operator_id,
                    "rationale": rationale,
                    "account_id": account_id,
                    "at": datetime.now(tz=UTC).isoformat(),
                }
            )
        except Exception:  # noqa: BLE001 — non-fatal
            pass
    return _redirect_with_flash(
        "/strategies", f"registry:promoted:{strategy_id}"
    )


def _redirect_with_flash(location: str, flash: str) -> RedirectResponse:
    response = RedirectResponse(url=location, status_code=303)
    response.set_cookie(
        "strategies-flash",
        flash,
        max_age=60,
        samesite="lax",
    )
    return response
