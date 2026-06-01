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

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.observability import (
    configure_logging,
    structured_log,
)
from trading_system.webapp.health import router as health_router
from trading_system.webapp.metrics import router as metrics_router
from trading_system.webapp.routers.views.settings import router as settings_view_router
from trading_system.webapp.job_queue import InProcessJobQueue, JobQueue
from trading_system.webapp.routers.api.bars import router as bars_api_router
from trading_system.webapp.routers.api.operator_tokens import (
    router as operator_tokens_api_router,
)
from trading_system.webapp.routers.api.backtests import router as backtests_router
from trading_system.webapp.routers.api.hypotheses import (
    router as hypotheses_api_router,
)
from trading_system.webapp.routers.api.inbox import router as inbox_api_router
from trading_system.webapp.routers.api.live_mode import router as live_mode_router
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
from trading_system.webapp.routers.views.hypotheses import (
    router as hypotheses_view_router,
)
from trading_system.webapp.routers.views.operator_tokens import (
    router as operator_tokens_view_router,
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
    # CR-019 step 2 — live-mode operator action slots
    # (REQ_F_LIV_008 / REQ_SDD_LIV_005). Each is a small Protocol
    # the live-runtime composition layer wires in at boot; tests
    # inject fakes.
    live_mode_controller: Any | None = None
    emergency_stop_controller: Any | None = None
    broker_reconnect_controller: Any | None = None
    # CR-027 — hypothesis-filing slots (REQ_F_QNT_007..010).
    # Strategy_lab/quant/ stays offline-only (REQ_NF_QNT_001);
    # webapp routes go through Protocol slots only (the concrete
    # adapter is wired at boot in operator code).
    hypothesis_filer: Any | None = None
    hypothesis_lister: Any | None = None
    improvement_report_lookup: Any | None = None
    # CR-029 — multi-instrument bar persistence slot
    # (REQ_F_PER_011..014). When wired, the paper-trading runtime's
    # tick fans out polled bars to the repository + the GET
    # /api/accounts/{aid}/bars route reads them back.
    instrument_bar_repository: Any | None = None
    # CR-019 §6 follow-up — paper-session metadata slot. When
    # wired, the wizard writes the session's inputs (universe /
    # strategy / instrument / starting capital / bar source) on
    # finish + the recovery wizard reads them back for one-click
    # resume after a webapp restart.
    paper_session_repository: Any | None = None
    # CR-024 §7 — operator-token revocation repository slot. When
    # wired, POST /api/operator/accounts/<aid>/tokens/<jti>/revoke
    # adds a row to the revocation list + the verifier consults
    # it BEFORE the TTL check.
    operator_token_revocation_repo: Any | None = None
    # CR-001 Phase B — notification fan-out slot. When wired,
    # safety/alert_system + meta-loop rejections / KS events
    # broadcast a payload to every configured channel
    # (inbox + Slack + email + local_log per the operator's
    # `config/notifications.yaml`). ``None`` ⇒ no broadcast
    # (notifications stay in the inbox only, via the existing
    # boot breadcrumb path).
    notification_fanout: Any | None = None
    # CR-032 — operator settings reload-pending state. ``None``
    # after a fresh boot; set by the settings view's save handler
    # to a ``ReloadPending`` dataclass when the YAML is rewritten.
    # NOT persisted across restarts — restart IS the reload.
    reload_pending: Any | None = None
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

    # REQ_SDS_CRS_001 + Phase-8 hardening C2 — every request
    # carries a correlation id; downstream structured_log() calls
    # inherit it via the LogContext ContextVar.
    from trading_system.webapp.middleware import CorrelationMiddleware

    app.add_middleware(CorrelationMiddleware)

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
    # CR-019 step 2 live-mode controller slots.
    app.state.live_mode_controller = state.live_mode_controller
    app.state.emergency_stop_controller = state.emergency_stop_controller
    app.state.broker_reconnect_controller = state.broker_reconnect_controller
    # CR-027 hypothesis-filing slots.
    app.state.hypothesis_filer = state.hypothesis_filer
    app.state.hypothesis_lister = state.hypothesis_lister
    app.state.improvement_report_lookup = state.improvement_report_lookup
    # CR-029 — instrument-bar repository slot.
    app.state.instrument_bar_repository = state.instrument_bar_repository
    # CR-019 §6 — paper-session metadata slot.
    app.state.paper_session_repository = state.paper_session_repository
    # CR-024 §7 — operator-token revocation slot.
    app.state.operator_token_revocation_repo = (
        state.operator_token_revocation_repo
    )
    # CR-001 Phase B — notification fan-out slot (channels +
    # retry policy + inbox subscription). ``None`` ⇒ no
    # broadcast; tests inject a stub or skip wiring.
    app.state.notification_fanout = state.notification_fanout
    # CR-032 — operator settings reload-pending slot. Default
    # is whatever WebappState was constructed with (typically
    # None); the settings view's save handler mutates this
    # directly via `request.app.state.reload_pending = ...`.
    app.state.reload_pending = state.reload_pending

    # Routers.
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(settings_view_router)
    app.include_router(live_mode_router)
    app.include_router(live_state_router)
    app.include_router(paper_state_router)
    app.include_router(inbox_api_router)
    app.include_router(hypotheses_api_router)
    app.include_router(bars_api_router)
    app.include_router(operator_tokens_api_router)
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
    app.include_router(hypotheses_view_router)
    app.include_router(operator_tokens_view_router)
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

    # Phase-8 hardening C2 — configure JSON-line structured
    # logging at boot so every request's correlation id flows
    # into the stderr stream the operator (or Docker driver)
    # tails. Operators who want the human-readable format set
    # TRADING_BOT_LOG_HUMAN=1.
    configure_logging(
        level=os.environ.get("TRADING_BOT_LOG_LEVEL", "INFO"),
        json_output=os.environ.get("TRADING_BOT_LOG_HUMAN", "").strip() == "",
    )
    structured_log(
        __import__("logging").getLogger(__name__),
        __import__("logging").INFO,
        "system",
        "webapp:boot",
        version="0.1.0",
    )

    secret_env = os.environ.get("TRADING_BOT_OPERATOR_SECRET")
    if not secret_env:
        raise RuntimeError(
            "webapp:missing_operator_secret: set "
            "TRADING_BOT_OPERATOR_SECRET before booting the webapp"
        )
    ttl_seconds = int(os.environ.get("TRADING_BOT_TOKEN_TTL_SECONDS", "86400"))
    # CR-024 — wire the revocation repo into the verifier so the
    # auth path consults `is_revoked(account_id, jti)` BEFORE the
    # TTL check (REQ_F_TOK_002 / REQ_SDD_TOK_002). The repo is
    # SQLite-backed; multi-process single-host deployments rely on
    # SQLite WAL semantics — committed revocations from any
    # process are visible to all other processes on their next
    # `is_revoked` call. Multi-host deployments are not supported
    # by the v1 persistence target (SQLite is per-host).
    operator_token_revocation_repo = (
        _operator_token_revocation_repo_for_default_app()
    )
    verifier = AccountScopedTokenVerifier(
        secret=secret_env.encode("utf-8"),
        ttl_seconds=ttl_seconds,
        revocation_lookup=operator_token_revocation_repo,
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
    # CR-019 §6 — once we know the paper-session metadata is
    # available, the boot-resume inbox entries can carry the
    # universe + strategy + instrument so the operator sees what
    # was running before the restart (instead of just an opaque
    # account_id).
    session_metadata_repo = _paper_session_repo_for_default_app()
    if portfolio_repo is not None:
        from datetime import UTC, datetime as _dt

        from trading_system.result import Ok as _Ok
        from trading_system.webapp.inbox import InboxEntry as _Entry

        result = registry.resume_from_persistence(portfolio_repo)
        if isinstance(result, _Ok) and result.value:
            for aid in result.value:
                meta_line = ""
                if session_metadata_repo is not None:
                    try:
                        meta_res = session_metadata_repo.get(aid)
                        if isinstance(meta_res, _Ok) and meta_res.value is not None:
                            r = meta_res.value
                            meta_line = (
                                f" (universe={r.universe}, "
                                f"strategy={r.strategy_id}, "
                                f"instrument={r.instrument_symbol})"
                            )
                    except Exception:  # noqa: BLE001 — defensive
                        meta_line = ""
                inbox.append(
                    _Entry(
                        at=_dt.now(tz=UTC),
                        category="paper-session",
                        code="session_discovered",
                        severity="info",
                        message=(
                            "Persisted paper session discovered at boot"
                            f"{meta_line}. Visit /operator/recovery "
                            "(or attach a fresh runtime) to resume ticking."
                        ),
                        account_id=str(aid),
                    )
                )
    # CR-029 — open the per-symbol bar repository so the runtime's
    # tick fan-out + the GET /api/accounts/{aid}/bars route both
    # land their writes / reads in the same SQLite file. ``None``
    # ⇒ persistence unconfigured (TRADING_BOT_PERSISTENCE_DB unset)
    # and the runtime keeps ticking without the fan-out.
    instrument_bar_repository = _instrument_bar_repo_for_default_app()
    paper_session_repository = _paper_session_repo_for_default_app()
    # operator_token_revocation_repo was computed earlier in this
    # function so the verifier could be wired with it; reuse the
    # same instance here so the rotation/revocation route + the
    # auth-check share one repo (one Connection per process).
    # CR-001 Phase B — build the notification fan-out around the
    # configured channels + the runtime-owned inbox. Dashboard
    # alerts (KS events, meta-loop rejections, anomalies) now
    # broadcast to every channel the operator opted into via
    # `config/notifications.yaml` AND land in the inbox so the
    # in-process recovery surface always shows them.
    notification_fanout = build_notification_fanout(inbox=inbox)
    return create_app(
        WebappState(
            token_verifier=verifier,
            live_state_reader=_default_live_state_reader(),
            paper_state_reader=_default_paper_state_reader(registry=registry),
            runtime_registry=registry,
            notification_inbox=inbox,
            strategy_registry_reader=strategy_registry,
            job_queue=queue,
            instrument_bar_repository=instrument_bar_repository,
            paper_session_repository=paper_session_repository,
            operator_token_revocation_repo=operator_token_revocation_repo,
            notification_fanout=notification_fanout,
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


def _persistence_connection():  # type: ignore[no-untyped-def]
    """Open a Connection against ``TRADING_BOT_PERSISTENCE_DB``
    and run pending migrations.

    Returns ``None`` when the env var is unset OR the DB file
    can't be opened. Migrations run **on every call** so a fresh
    or new-schema DB picks up the CR-029 0009 migration without
    operator intervention.
    """
    import os
    from pathlib import Path

    from trading_system.persistence.connection import Connection
    from trading_system.persistence.migrations.runner import MigrationRunner

    db_path_raw = os.environ.get("TRADING_BOT_PERSISTENCE_DB", "")
    if not db_path_raw:
        return None
    db_path = Path(db_path_raw)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    result = Connection.open(db_path)
    if not hasattr(result, "is_ok") or not result.is_ok():
        return None
    conn = result.unwrap()
    migrations_dir = (
        Path(__file__).resolve().parent.parent / "persistence" / "migrations"
    )
    runner_result = MigrationRunner(
        conn=conn, migrations_dir=migrations_dir
    ).run()
    if not hasattr(runner_result, "is_ok") or not runner_result.is_ok():
        # Migration failure ⇒ close the connection + skip persistence.
        # The webapp continues without persistence rather than crashing.
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return None
    return conn


def _portfolio_repo_for_resume():  # type: ignore[no-untyped-def]
    """Open the operator-configured ``PortfolioRepository`` so
    ``RuntimeRegistry.resume_from_persistence`` can discover
    ``paper-*`` rows at boot (REQ_SDD_WEB2_005).

    Returns ``None`` when ``TRADING_BOT_PERSISTENCE_DB`` is unset
    or the SQLite file doesn't exist — boot proceeds without
    resume rather than aborting on a misconfiguration.
    """
    from trading_system.persistence.repositories.portfolio import (
        PortfolioRepository,
    )

    conn = _persistence_connection()
    if conn is None:
        return None
    return PortfolioRepository(conn=conn)


def _instrument_bar_repo_for_default_app():  # type: ignore[no-untyped-def]
    """CR-029 (REQ_F_PER_011..014) — open the
    ``InstrumentBarRepository`` for ``default_app()``. Returns
    ``None`` when persistence is unconfigured; the runtime
    fan-out + the GET /api/accounts/{aid}/bars route both
    surface their "saving disabled" path in that case."""
    from trading_system.persistence.repositories.instrument_bars import (
        InstrumentBarRepository,
    )

    conn = _persistence_connection()
    if conn is None:
        return None
    return InstrumentBarRepository(conn=conn)


def _paper_session_repo_for_default_app():  # type: ignore[no-untyped-def]
    """CR-019 §6 — open the ``PaperSessionRepository`` for
    ``default_app()``. Returns ``None`` when persistence is
    unconfigured; the wizard skips the metadata write + the
    recovery wizard falls back to its pre-§6 behaviour
    (operator re-supplies the inputs)."""
    from trading_system.persistence.repositories.paper_sessions import (
        PaperSessionRepository,
    )

    conn = _persistence_connection()
    if conn is None:
        return None
    return PaperSessionRepository(conn=conn)


def _operator_token_revocation_repo_for_default_app():  # type: ignore[no-untyped-def]
    """CR-024 §7 — open the ``OperatorTokenRevocationRepository``
    for ``default_app()``. Returns ``None`` when persistence is
    unconfigured; the rotation endpoint still works
    (rotate_secret is in-process), but the revocation endpoint
    surfaces `webapp:operator_token_revocation_repo_missing`."""
    from trading_system.persistence.repositories.token_revocations import (
        OperatorTokenRevocationRepository,
    )

    conn = _persistence_connection()
    if conn is None:
        return None
    return OperatorTokenRevocationRepository(conn=conn)


def _default_config_dir() -> Path:
    """Resolve the config directory ``default_app()`` reads YAMLs from.

    Honours ``TRADING_BOT_CONFIG_DIR`` when set; otherwise falls
    back to the repo-bundled ``config/`` directory next to
    ``trading_system/``. The bundled directory ships sample
    YAMLs (notifications, risk, phases, etc.) so a vanilla
    `python -m trading_system` boot picks up a working set
    of defaults.
    """
    env_dir = os.environ.get("TRADING_BOT_CONFIG_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parent.parent.parent / "config"


def build_notification_fanout(
    *, inbox: Any, config_dir: Path | None = None
) -> Any:
    """CR-001 Phase B — assemble the ``NotificationFanOut`` for the
    webapp's boot path.

    The fanout subscribes ALL channels declared in
    ``config/notifications.yaml`` (local_log + optional slack +
    optional email) PLUS the runtime-owned ``inbox`` channel so
    dashboard alerts always land in the operator's inbox AND on
    their configured external channels simultaneously.

    Behaviour on config errors / missing YAML:
    - Missing file ⇒ defaults from ``NotificationsConfig()`` —
      local_log channel only. Webapp keeps booting.
    - Schema / invariant Err on present file ⇒ falls back to
      defaults + emits a structured-log envelope so the
      operator notices on the next dashboard paint. Webapp
      still boots; the inbox channel is always subscribed via
      ``extra``.
    """
    from trading_system.notifications.fanout import (
        NotificationFanOut,
        RetryPolicy,
    )
    from trading_system.notifications.loader import (
        NotificationsConfig,
        build_channels,
        load_notifications_config,
    )

    cd = config_dir or _default_config_dir()
    yaml_path = Path(cd) / "notifications.yaml"
    cfg: NotificationsConfig
    if yaml_path.exists():
        load_result = load_notifications_config(yaml_path)
        if hasattr(load_result, "is_ok") and load_result.is_ok():
            cfg = load_result.value
        else:
            # Categorised Err on a present-but-broken YAML. Fall
            # back to defaults rather than crashing the webapp;
            # the structured-log envelope surfaces the issue.
            structured_log(
                logging.getLogger(__name__),
                logging.WARNING,
                "config",
                "notifications_yaml_invalid_falling_back_to_defaults",
                path=str(yaml_path),
                reason=getattr(load_result, "error", "<unknown>"),
            )
            cfg = NotificationsConfig()
    else:
        cfg = NotificationsConfig()

    channels = build_channels(cfg, extra=(inbox,))
    retry = RetryPolicy(
        max_attempts=cfg.retry.max_attempts,
        base_delay_seconds=cfg.retry.base_delay_seconds,
        growth_factor=cfg.retry.growth_factor,
    )
    return NotificationFanOut(
        channels=tuple(channels), retry_policy=retry
    )


