"""FastAPI application factory for the CR-017 webapp.

REQ refs:
- REQ_F_FAS_001 — route-for-route parity with the CR-004 stdlib path.
- REQ_F_FAS_002 — server-rendered HTMX frontend.
- REQ_F_FAS_004 — OpenAPI auto-doc at ``/docs`` + ``/redoc``.
- REQ_F_FAS_005 — Bearer auth via ``AccountScopedTokenVerifier``.
- REQ_SDS_FAS_001 — L7 placement; closed import graph audited by
  ``tests/webapp/test_structural.py``.
- REQ_SDD_FAS_001 — closed import graph; no execution / safety /
  risk / strategy_lab / data direct imports.

Phase A scope:
- ``GET /health`` (no auth — container HEALTHCHECK).
- ``GET /api/accounts/{account_id}/live-state`` (household claim).
- ``POST /api/registry/{strategy_id}/promote`` (per-account claim).
- ``GET /`` HTMX dashboard with 5s polling on live-state.
- OpenAPI auto-doc at ``/docs`` / ``/redoc`` / ``/openapi.json``.

Phase B follow-ups (deferred):
- SSE live-state push at ``/events/live-state``.
- Cookie-session auth (``POST /api/session``).
- Async backtest invocation + JobQueue.
- The remaining CR-004 read endpoints (summary, registry, backtests,
  improvement-reports).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.webapp.health import router as health_router
from trading_system.webapp.routers.api.live_state import router as live_state_router
from trading_system.webapp.routers.api.registry import router as registry_router
from trading_system.webapp.routers.views.dashboard import router as dashboard_router


_PACKAGE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PACKAGE_DIR / "static"
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"


@dataclass(slots=True)
class WebappState:
    """State the factory attaches to the FastAPI app at startup.

    The DI graph reaches every dependency through
    ``request.app.state.<name>`` so the routes stay free of
    module-level globals and tests can wire in fakes per-app.
    """

    token_verifier: AccountScopedTokenVerifier
    live_state_reader: Any | None = None
    registry_promoter: Any | None = None
    promotion_audit_notifier: Any | None = None
    templates: Jinja2Templates = field(init=False)

    def __post_init__(self) -> None:
        self.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def create_app(state: WebappState) -> FastAPI:
    """Build the FastAPI application with the routers + static mount.

    The caller passes a fully-populated ``WebappState`` —
    Phase A's wiring is operator-driven (the production deploy fills
    every field; tests fill the subset their endpoints exercise).
    """
    app = FastAPI(
        title="trading-bot webapp",
        description=(
            "FastAPI surface for the trading-bot. Phase A ships the "
            "live-state read + registry-promotion mutation + HTMX "
            "dashboard. Phase B adds SSE + JobQueue + cookie sessions."
        ),
        version="0.1.0",
    )

    # Mount static assets first so the templates' url_for resolves.
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    # State injection — every router reads from app.state at request time.
    app.state.token_verifier = state.token_verifier
    app.state.templates = state.templates
    app.state.live_state_reader = state.live_state_reader
    app.state.registry_promoter = state.registry_promoter
    app.state.promotion_audit_notifier = state.promotion_audit_notifier

    # Routers.
    app.include_router(health_router)
    app.include_router(live_state_router)
    app.include_router(registry_router)
    app.include_router(dashboard_router)

    return app


def default_app() -> FastAPI:
    """ASGI entry point for ``uvicorn trading_system.webapp.app:default_app
    --factory``.

    The Dockerfile's ``CMD`` invokes this factory. It reads operator
    secrets from environment variables so the deploy stays out of the
    image layers:

      - ``TRADING_BOT_OPERATOR_SECRET`` (required) — HMAC-SHA256 key
        the ``AccountScopedTokenVerifier`` consumes.
      - ``TRADING_BOT_TOKEN_TTL_SECONDS`` (optional, default ``300``).

    ``live_state_reader`` / ``registry_promoter`` /
    ``promotion_audit_notifier`` start unset; Phase B wires them via a
    follow-up factory that consumes ``config/webapp.yaml`` + CR-008
    repositories. Endpoints that depend on those slots fail-fast with
    a 500 + a categorised ``webapp:reader_missing`` /
    ``webapp:registry_promoter_missing`` body so the operator
    diagnoses a half-wired deployment immediately.
    """
    import os

    secret_env = os.environ.get("TRADING_BOT_OPERATOR_SECRET")
    if not secret_env:
        raise RuntimeError(
            "webapp:missing_operator_secret: set "
            "TRADING_BOT_OPERATOR_SECRET before booting the webapp"
        )
    ttl_seconds = int(os.environ.get("TRADING_BOT_TOKEN_TTL_SECONDS", "300"))
    verifier = AccountScopedTokenVerifier(
        secret=secret_env.encode("utf-8"),
        ttl_seconds=ttl_seconds,
    )
    return create_app(WebappState(token_verifier=verifier))
