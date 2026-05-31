"""CR-024 §7 — operator-token management view route.

  GET /operator/tokens

Renders the operator-token management panel: a "Rotate secret"
button + a per-account revocation table. The actual mutations
go through the JSON API endpoints
(``/api/operator/rotate-secret`` and
``/api/operator/accounts/{aid}/tokens/{jti}/revoke``); this view
is just the operator-facing form surface.

REQ refs:
- REQ_F_TOK_002 — revocation list display.
- REQ_F_TOK_003 — rotate-secret control.
- REQ_NF_WEB2_001 — no SPA, no Node toolchain.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from trading_system.models.identifiers import AccountId
from trading_system.result import Ok
from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim
from trading_system.webapp.fragments import fragment_context


router = APIRouter(prefix="/operator")


def _require_auth(request: Request) -> bool:
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    return (
        verifier is not None
        and token is not None
        and verify_any_valid_claim(verifier, token)
    )


@router.get(
    "/tokens", response_class=HTMLResponse, name="operator-tokens"
)
def get_operator_tokens(request: Request, account_id: str = "default"):
    """Render the operator-token management panel.

    Two sub-sections:
    - **Rotate secret** — household-only POST control; rotating
      generates a fresh secret server-side + displays it ONCE.
    - **Revoked tokens** — per-account table of jti's the
      revocation list carries. The "Revoke" form takes a fresh
      jti + posts to the JSON API.
    """
    if not _require_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    templates = request.app.state.templates
    rows = _load_revocations(request, account_id=AccountId(account_id))
    return templates.TemplateResponse(
        request=request,
        name="operator_tokens.html",
        context={
            "account_id": account_id,
            "revocations": rows,
            **fragment_context(request),
        },
    )


def _load_revocations(
    request: Request, *, account_id: AccountId
) -> list[dict[str, Any]]:
    """Best-effort: empty list when the slot isn't wired (the
    deployment hasn't configured persistence yet)."""
    repo = getattr(request.app.state, "operator_token_revocation_repo", None)
    if repo is None or not hasattr(repo, "list_all"):
        return []
    try:
        result = repo.list_all(account_id=account_id)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(result, Ok):
        return []
    return [
        {
            "jti": str(getattr(r, "jti", "")),
            "reason": str(getattr(r, "reason", "")),
            "revoked_at": (
                getattr(r, "revoked_at", "").isoformat()
                if hasattr(getattr(r, "revoked_at", ""), "isoformat")
                else str(getattr(r, "revoked_at", ""))
            ),
        }
        for r in result.value
    ]
