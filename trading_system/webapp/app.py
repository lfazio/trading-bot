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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.webapp.health import router as health_router
from trading_system.webapp.job_queue import InProcessJobQueue, JobQueue
from trading_system.webapp.routers.api.backtests import router as backtests_router
from trading_system.webapp.routers.api.inbox import router as inbox_api_router
from trading_system.webapp.routers.api.live_state import router as live_state_router
from trading_system.webapp.routers.api.paper_state import router as paper_state_router
from trading_system.webapp.routers.api.registry import router as registry_router
from trading_system.webapp.routers.api.session import router as session_router
from trading_system.webapp.routers.views.dashboard import router as dashboard_router
from trading_system.webapp.routers.views.jobs import router as jobs_view_router
from trading_system.webapp.routers.views.login import router as login_router
from trading_system.webapp.routers.views.notifications import (
    router as notifications_router,
)
from trading_system.webapp.routers.views.onboarding import router as onboarding_router
from trading_system.webapp.routers.views.paper_session import (
    router as paper_session_router,
)
from trading_system.webapp.routers.views.recovery import (
    router as recovery_router,
)
from trading_system.webapp.routers.views.reports import router as reports_router
from trading_system.webapp.routers.views.strategies import (
    router as strategies_router,
)
from trading_system.webapp.sse import router as sse_router


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
    paper_state_reader: Any | None = None
    runtime_registry: Any | None = None
    notification_inbox: Any | None = None
    recovery_gate: Any | None = None
    strategy_registry_reader: Any | None = None
    registry_promoter: Any | None = None
    promotion_audit_notifier: Any | None = None
    job_queue: JobQueue | None = None
    templates: Jinja2Templates = field(init=False)

    def __post_init__(self) -> None:
        self.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def create_app(state: WebappState) -> FastAPI:
    """Build the FastAPI application with the routers + static mount.

    The caller passes a fully-populated ``WebappState`` —
    Phase A's wiring is operator-driven (the production deploy fills
    every field; tests fill the subset their endpoints exercise).
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """REQ_SDD_FAS_005 — owns long-lived background resources.

        Starts the paper-trading tick driver when a runtime
        registry is wired so the dashboard panel paints live
        equity ticks the moment the onboarding wizard finishes;
        stops the driver + closes the JobQueue executor on
        shutdown.
        """
        tick_driver = None
        registry = getattr(application.state, "runtime_registry", None)
        if registry is not None:
            from trading_system.webapp.runtimes.tick_driver import (
                PaperTickDriver,
            )

            tick_driver = PaperTickDriver(registry=registry)
            tick_driver.start()
            application.state.tick_driver = tick_driver
        try:
            yield
        finally:
            if tick_driver is not None:
                await tick_driver.stop()
            queue = getattr(application.state, "job_queue", None)
            if queue is not None and hasattr(queue, "close"):
                queue.close()

    app = FastAPI(
        title="trading-bot webapp",
        description=(
            "FastAPI surface for the trading-bot. Phase A ships the "
            "live-state read + registry-promotion mutation + HTMX "
            "dashboard. Phase B adds SSE + JobQueue + cookie sessions."
        ),
        version="0.1.0",
        lifespan=lifespan,
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
    app.state.paper_state_reader = state.paper_state_reader
    app.state.runtime_registry = state.runtime_registry
    app.state.notification_inbox = state.notification_inbox
    app.state.recovery_gate = state.recovery_gate
    app.state.strategy_registry_reader = state.strategy_registry_reader
    app.state.registry_promoter = state.registry_promoter
    app.state.promotion_audit_notifier = state.promotion_audit_notifier
    app.state.job_queue = state.job_queue

    # Routers.
    app.include_router(health_router)
    app.include_router(live_state_router)
    app.include_router(paper_state_router)
    app.include_router(inbox_api_router)
    app.include_router(registry_router)
    app.include_router(backtests_router)
    app.include_router(session_router)
    app.include_router(sse_router)
    app.include_router(login_router)
    app.include_router(notifications_router)
    app.include_router(onboarding_router)
    app.include_router(paper_session_router)
    app.include_router(recovery_router)
    app.include_router(reports_router)
    app.include_router(strategies_router)
    app.include_router(dashboard_router)
    app.include_router(jobs_view_router)

    return app


def default_app() -> FastAPI:
    """ASGI entry point for ``uvicorn trading_system.webapp.app:default_app
    --factory``.

    The Dockerfile's ``CMD`` invokes this factory. It reads operator
    secrets from environment variables so the deploy stays out of the
    image layers:

      - ``TRADING_BOT_OPERATOR_SECRET`` (required) — HMAC-SHA256 key
        the ``AccountScopedTokenVerifier`` consumes.
      - ``TRADING_BOT_TOKEN_TTL_SECONDS`` (optional, default ``86400``
        = 24h). Matches ``tools/issue_operator_token.py``'s default
        so a freshly-minted household token works for the rest of
        the operator's day without re-issuance. Operators who want
        short-lived tokens (CI flows, paranoid prod) override the
        env var.

    A ``RuntimeLiveStateReader`` over an unattached
    ``RuntimeStateBag`` wires in so the dashboard is end-to-end
    runnable on a fresh container. With no Portfolio/Safety/Phase
    references attached the reader returns bootstrap defaults
    (Phase 1, KS ACTIVE, zero positions, equity =
    ``TRADING_BOT_STARTING_CAPITAL`` env var or ``0``). Operators
    attach a live trading process by constructing a populated bag
    via a small wiring script that re-builds the reader. The
    ``registry_promoter`` slot stays unset — the promotion endpoint
    surfaces a 500 ``webapp:registry_promoter_missing`` until Phase
    B wires the CR-008 ``RegistryRepository``.
    """
    import os

    secret_env = os.environ.get("TRADING_BOT_OPERATOR_SECRET")
    if not secret_env:
        raise RuntimeError(
            "webapp:missing_operator_secret: set "
            "TRADING_BOT_OPERATOR_SECRET before booting the webapp"
        )
    ttl_seconds = int(os.environ.get("TRADING_BOT_TOKEN_TTL_SECONDS", "86400"))
    verifier = AccountScopedTokenVerifier(
        secret=secret_env.encode("utf-8"),
        ttl_seconds=ttl_seconds,
    )
    workers = int(os.environ.get("TRADING_BOT_JOB_WORKERS", "2"))
    queue = InProcessJobQueue(workers=workers)
    from trading_system.webapp.inbox import InboxChannel
    from trading_system.webapp.runtimes.paper_trading import RuntimeRegistry
    from trading_system.webapp.strategy_registry_reader import (
        StaticStrategyRegistryReader,
    )

    registry = RuntimeRegistry()
    inbox = InboxChannel()
    strategy_registry = StaticStrategyRegistryReader()
    # REQ_SDD_WEB2_005 — resume previously-persisted paper sessions
    # at boot so a webapp restart doesn't lose the operator's
    # session list. v1 is discovery-only (the operator picks one
    # of the returned ids from the recovery wizard); the resumed
    # account_ids are surfaced as a one-line breadcrumb in the
    # inbox so the operator notices them on next paint.
    portfolio_repo = _portfolio_repo_for_resume()
    if portfolio_repo is not None:
        from datetime import UTC, datetime as _dt

        from trading_system.result import Ok as _Ok
        from trading_system.webapp.inbox import InboxEntry as _Entry

        result = registry.resume_from_persistence(portfolio_repo)
        if isinstance(result, _Ok) and result.value:
            for aid in result.value:
                inbox.append(
                    _Entry(
                        at=_dt.now(tz=UTC),
                        category="paper-session",
                        code="session_discovered",
                        severity="info",
                        message=(
                            "Persisted paper session discovered at boot. "
                            "Visit /operator/recovery (or attach a fresh "
                            "runtime) to resume ticking."
                        ),
                        account_id=str(aid),
                    )
                )
    return create_app(
        WebappState(
            token_verifier=verifier,
            live_state_reader=_default_live_state_reader(),
            paper_state_reader=_default_paper_state_reader(registry=registry),
            runtime_registry=registry,
            notification_inbox=inbox,
            strategy_registry_reader=strategy_registry,
            job_queue=queue,
        )
    )


def _default_live_state_reader():  # type: ignore[no-untyped-def]
    """Build the default ``LiveStateReader`` for ``default_app()``.

    The reader pulls live state from a ``RuntimeStateBag`` whose
    view fields stay ``None`` at boot — operators attach a Portfolio
    / Safety / Phase view from a co-located trading process via a
    small wiring script.

    With no views attached, the reader returns the bootstrap
    defaults (Phase 1, KS ACTIVE, zero positions, equity =
    ``TRADING_BOT_STARTING_CAPITAL`` env var or ``0``). This makes
    the dashboard honest about its data source: empty deployment
    shows zero equity rather than a fake ``10000.00``.
    """
    import os
    from decimal import Decimal

    from trading_system.webapp.state_readers import (
        RuntimeLiveStateReader,
        RuntimeStateBag,
    )

    starting_capital_raw = os.environ.get("TRADING_BOT_STARTING_CAPITAL", "0")
    try:
        starting_capital = Decimal(starting_capital_raw)
    except (ValueError, ArithmeticError):
        starting_capital = Decimal("0")
    return RuntimeLiveStateReader(
        bag=RuntimeStateBag(
            bootstrap_equity_after_tax=starting_capital,
        ),
    )


def _default_paper_state_reader(*, registry=None):  # type: ignore[no-untyped-def]
    """Build the default ``PaperStateReader`` for ``default_app()``.

    Reads from the supplied ``RuntimeRegistry``. When called
    without one, constructs a fresh empty registry so a partial
    deployment still has a working reader; production callers
    pass the same registry instance that the onboarding wizard
    writes to so both surfaces see the same set of live
    runtimes.
    """
    from trading_system.webapp.paper_state_reader import (
        RuntimePaperStateReader,
    )
    from trading_system.webapp.runtimes.paper_trading import RuntimeRegistry

    return RuntimePaperStateReader(registry=registry or RuntimeRegistry())


def _portfolio_repo_for_resume():  # type: ignore[no-untyped-def]
    """Open the operator-configured ``PortfolioRepository`` so
    ``RuntimeRegistry.resume_from_persistence`` can discover
    ``paper-*`` rows at boot (REQ_SDD_WEB2_005).

    Returns ``None`` when ``TRADING_BOT_PERSISTENCE_DB`` is unset
    or the SQLite file doesn't exist — boot proceeds without
    resume rather than aborting on a misconfiguration.
    """
    import os
    from pathlib import Path

    from trading_system.persistence.connection import Connection
    from trading_system.persistence.repositories.portfolio import (
        PortfolioRepository,
    )

    db_path_raw = os.environ.get("TRADING_BOT_PERSISTENCE_DB", "")
    if not db_path_raw:
        return None
    db_path = Path(db_path_raw)
    if not db_path.exists():
        return None
    result = Connection.open(db_path)
    if not hasattr(result, "is_ok") or not result.is_ok():
        return None
    return PortfolioRepository(conn=result.unwrap())


