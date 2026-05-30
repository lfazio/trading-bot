"""CR-027 — Operator hypothesis-filing view route.

GET /strategies/hypotheses

Three sections per REQ_SDD_QNT_009:
- file-new form (HTMX hx-post to /api/hypotheses)
- PENDING / REJECTED table with categorised reasons
- VALIDATED table sorted by created_at DESC

The route consumes the hypothesis layer through Protocol-typed
``app.state.hypothesis_lister`` so the webapp imports stay
structurally clean (REQ_NF_QNT_001 — `strategy_lab/quant/` stays
offline-only; REQ_SDD_FAS_001 — view routers SHALL NOT reach
``strategy_lab.*`` directly).

REQ refs:
- REQ_F_QNT_007 — view route + three sections.
- REQ_SDD_QNT_009 — HTMX form + tables.
- REQ_NF_WEB2_001 — no SPA / no Node.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from trading_system.models.identifiers import AccountId
from trading_system.result import Ok
from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim
from trading_system.webapp.fragments import fragment_context


router = APIRouter(prefix="/strategies")


def _require_auth(request: Request) -> bool:
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    return (
        verifier is not None
        and token is not None
        and verify_any_valid_claim(verifier, token)
    )


@router.get(
    "/hypotheses", response_class=HTMLResponse, name="strategies-hypotheses"
)
def get_hypotheses_view(request: Request, account_id: str = "default"):
    """REQ_F_QNT_007 / REQ_SDD_QNT_009 — render the three sections."""
    if not _require_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    templates = request.app.state.templates
    pending_rejected, validated = _load_hypotheses(
        request, account_id=AccountId(account_id)
    )
    return templates.TemplateResponse(
        request=request,
        name="hypotheses.html",
        context={
            "account_id": account_id,
            "pending_rejected": pending_rejected,
            "validated": validated,
            "flash": request.cookies.get("hypotheses-flash", "") or None,
            **fragment_context(request),
        },
    )


def _load_hypotheses(
    request: Request, *, account_id: AccountId
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the hypothesis library (best-effort) and split into
    PENDING/REJECTED vs VALIDATED rows. Empty lists when the lister
    slot is unwired (the dashboard surface stays operable on a
    fresh container).

    Rows are plain dicts (matches the API surface). Each carries
    ``id`` / ``claim`` / ``metric`` / ``state`` / ``created_at``;
    the lister side fills the canonical Hypothesis fields.
    """
    lister = getattr(request.app.state, "hypothesis_lister", None)
    if lister is None or not hasattr(lister, "list_filed"):
        return [], []
    try:
        result = lister.list_filed(account_id=account_id)
    except Exception:  # noqa: BLE001 — defensive
        return [], []
    if not isinstance(result, Ok):
        return [], []
    rows = result.value
    pending_rejected: list[dict[str, Any]] = []
    validated: list[dict[str, Any]] = []
    # Sort by created_at DESC.
    for row in sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True):
        state = str(row.get("state", "")).lower()
        if state == "validated":
            validated.append(row)
        else:
            pending_rejected.append(row)
    return pending_rejected, validated
