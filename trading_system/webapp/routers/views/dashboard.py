"""HTMX dashboard page — Phase A scope.

REQ refs:
- REQ_F_FAS_002 — server-rendered HTML with HTMX interactivity; no
  client-side JS bundle beyond ``htmx.min.js``.

The dashboard renders ``GET /`` and hydrates the live-state block via
HTMX `hx-get` on a poll trigger (SSE auto-refresh moves in Phase B
once `sse-starlette` wiring lands).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse, RedirectResponse

from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim
from trading_system.webapp.fragments import fragment_context


router = APIRouter()


def _templates(request: Request) -> Jinja2Templates:
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise RuntimeError("webapp:templates_missing")
    return templates


@router.get("/", response_class=HTMLResponse, name="dashboard")
def get_dashboard(request: Request):
    """Render the Phase-B dashboard.

    Browser path is graceful: a missing or invalid session cookie
    redirects to ``/login`` rather than returning a raw 401 JSON.
    Tooling (curl + httpx) still gets the JSON-shaped 401 from the
    other endpoints; only the HTML entry point is browser-friendly.

    Auth: any valid token claim (household OR per-account) is
    accepted for the VIEW. Mutation endpoints (registry promotion)
    keep per-account scoping via ``require_account_token``.
    """
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verify_any_valid_claim(verifier, token)
    ):
        return RedirectResponse(url="/login", status_code=303)
    # Choose which session the panel SSE-connects to, in order:
    # 1. ``?account_id=<id>`` query param (just-finished wizard
    #    redirect, or a manual switch from the multi-account
    #    selector).
    # 2. ``active-paper-session`` cookie (last finished wizard,
    #    1h lifetime) — so the operator refreshing ``/`` or
    #    coming back later still sees their session.
    # 3. ``"default"`` household claim — pre-onboarding state.
    account_id = (
        request.query_params.get("account_id", "").strip()
        or request.cookies.get("active-paper-session", "").strip()
        or "default"
    )
    # Surface every currently-live paper session in a switcher
    # so the operator can hop between them without retyping the
    # query string. Defensive against an unwired registry.
    registry = getattr(request.app.state, "runtime_registry", None)
    live_paper_sessions: tuple[str, ...] = ()
    if registry is not None and hasattr(registry, "live_account_ids"):
        try:
            live_paper_sessions = tuple(str(a) for a in registry.live_account_ids())
        except Exception:  # noqa: BLE001 — defensive
            live_paper_sessions = ()
    # REQ_F_WEB2_002 — three-position mode switch. ``live`` is
    # disabled until the live-trading amendment lands (gated on
    # REQ_F_BRK_003 broker-adapter selection); the template renders
    # the disabled button with the documented tooltip.
    mode_raw = request.query_params.get("mode", "paper").strip().lower()
    if mode_raw not in ("paper", "backtest", "live"):
        mode_raw = "paper"
    # CR-019 step 2 / REQ_F_LIV_002 / REQ_SDD_LIV_004 — the dashboard's
    # `live` mode chip enablement is a function of:
    #   (a) `var/live-preflight.json` exists AND outcome=="ok" AND
    #       checked_at within the configured staleness window (30s);
    #   (b) `config/system.yaml.broker.adapter != "local"`.
    # The status is exposed to the template as `live_mode_status` —
    # either {"enabled": True, "checked_at": ...} or
    # {"enabled": False, "reason": "..."}.
    live_mode_status = _live_mode_status(request)
    # REQ_F_WEB2_008 — household-drawdown indicator + per-account
    # equity roll-up. Computed only when ≥ 2 live sessions exist
    # so single-account dashboards stay byte-identical.
    household = None
    paper_reader = getattr(request.app.state, "paper_state_reader", None)
    if (
        registry is not None
        and paper_reader is not None
        and len(live_paper_sessions) >= 2
    ):
        from datetime import UTC, datetime

        from trading_system.webapp.household import household_snapshot

        household = household_snapshot(
            registry, paper_reader, as_of=datetime.now(tz=UTC)
        )
    return _templates(request).TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "account_id": account_id,
            "live_paper_sessions": live_paper_sessions,
            "active_mode": mode_raw,
            "household": household,
            "live_mode_status": live_mode_status,
            **fragment_context(request),
        },
    )


# Staleness window for the preflight artefact (REQ_SDD_LIV_004 default
# 30 s). Exposed at module level so tests + future config tightens
# the window without touching the handler.
_PREFLIGHT_STALENESS_SECONDS = 30


def _live_mode_status(request: Request) -> dict:
    """Compute the dashboard's `live` chip enablement state.

    Reads:
    - ``var/live-preflight.json`` written by ``trading-bot
      live-preflight`` (REQ_F_LIV_005).
    - ``config/system.yaml``'s ``broker.adapter`` field; ``local``
      is the lifecycle baseline + SHALL NOT enable live mode
      (REQ_F_LIV_002 / REQ_SDD_LIV_004).

    Returns a dict the template branches on. Never raises — every
    failure surfaces as ``{"enabled": False, "reason": ...}`` so
    a missing artefact / unreadable config keeps the chip disabled
    with a clear tooltip.
    """
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    artefact_path = getattr(
        request.app.state, "live_preflight_artefact", None
    ) or Path("var/live-preflight.json")
    artefact_path = Path(artefact_path)
    if not artefact_path.is_file():
        return {
            "enabled": False,
            "reason": "live:preflight_artefact_missing",
        }
    try:
        payload = json.loads(artefact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {
            "enabled": False,
            "reason": f"live:preflight_artefact_unreadable:{e}",
        }
    if payload.get("outcome") != "ok":
        return {
            "enabled": False,
            "reason": "live:preflight_failed",
        }
    # Staleness check.
    try:
        checked_at = datetime.fromisoformat(payload["checked_at"])
    except (KeyError, ValueError):
        return {
            "enabled": False,
            "reason": "live:preflight_bad_timestamp",
        }
    now = datetime.now(tz=UTC)
    if (now - checked_at).total_seconds() > _PREFLIGHT_STALENESS_SECONDS:
        return {
            "enabled": False,
            "reason": "live:preflight_stale",
        }
    # Broker selector check.
    broker_selector = _broker_selector(request)
    if not broker_selector or broker_selector == "local":
        return {
            "enabled": False,
            "reason": "live:broker_local",
        }
    return {
        "enabled": True,
        "checked_at": payload["checked_at"],
        "broker_selector": broker_selector,
    }


def _broker_selector(request: Request) -> str:
    """Read the broker selector stashed on `app.state.broker_selector`
    by the boot wiring. The dashboard view is the wrong layer to
    read `config/system.yaml` directly (structural audit forbids
    `trading_system.config.*` reach from `webapp/routers/views/`);
    operators wire the selector into `app.state` once at boot.

    Defaults to `"local"` (the lifecycle baseline) so an
    unwired deployment keeps the live chip disabled per
    REQ_F_LIV_002 / REQ_SDD_LIV_004.
    """
    cached = getattr(request.app.state, "broker_selector", None)
    if cached:
        return str(cached).strip().lower()
    return "local"
