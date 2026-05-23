"""Onboarding wizard view router — CR-019 / REQ_F_WEB2_001 +
REQ_SDD_WEB2_002.

3-step linear flow, server-rendered, no SPA:

  GET  /onboarding              → step 1 (capital + universe)
  POST /onboarding/step2        → validate step 1, render step 2
  POST /onboarding/step3        → validate step 2, render step 3
  POST /onboarding/finish       → finalise the session + redirect
  POST /onboarding/cancel       → clear cookie + redirect

Each POST validates the partial form, persists the wizard state
in an HMAC-signed ``httponly`` ``wizard-state`` cookie (signed by
the existing ``AccountScopedTokenVerifier``), and renders the
next step.

The outermost wizard container carries ``role="dialog"`` +
``aria-modal="true"`` (REQ_SDD_WEB2_002). Focus-trap helper
(REQ_SDD_WEB2_007) is mounted by ``base.html`` in a follow-up
slice — the static markup is keyboard-reachable today.

Authentication: this slice keeps the wizard *unauthenticated*
because it is the first-boot flow — there is no operator token
yet at the moment the operator clicks "Start". A future
amendment may gate it behind a deployment-secret check; for now
operators are expected to firewall the deployment.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from trading_system.models.identifiers import InstrumentId, StrategyId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.webapp.runtimes.paper_trading import (
    PAPER_ACCOUNT_PREFIX,
    PaperTradingSession,
    new_paper_account_id,
)
from trading_system.webapp.runtimes.simulated_bar_source import (
    SimulatedBarSource,
    SimulatedMarketDataProvider,
)
from trading_system.webapp.fragments import fragment_context
from trading_system.webapp.wizard_state import (
    ALLOWED_STRATEGIES,
    ALLOWED_UNIVERSES,
    WIZARD_COOKIE_NAME,
    WizardState,
    decode_state,
    encode_state,
    is_valid_capital,
)


# v1 — one default instrument per universe so the wizard can hand the
# runtime a concrete ``Instrument`` without an extra "pick a symbol"
# step. Operators can pin their own symbol in a follow-up CR.
_DEFAULT_INSTRUMENTS: dict[str, Stock] = {
    "eu-dividend-starter": Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    ),
    # AC.PA (Accor) is the alphabetical first of the validated
    # CAC 40 list in data/universes/cac40.yaml. The runtime
    # loader resolves the actual first stock at session-start
    # time, so this is only used when the YAML can't be read.
    "cac40": Stock(
        id=InstrumentId("AC.PA"),
        symbol="AC",
        exchange="PA",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="FR0000120404",
        sector="consumer-discretionary",
        country="FR",
    ),
}


router = APIRouter(prefix="/onboarding")


def _build_bar_source(kind: str, *, instrument, account_id):  # type: ignore[no-untyped-def]
    """Construct the requested BarSource. The yfinance path
    delegates to the runtime-layer helper so the views layer
    stays free of any ``trading_system.data.*`` reach
    (structural-audit constraint)."""
    if kind == "yfinance":
        from trading_system.webapp.runtimes.yfinance_bar_source import (
            build_yfinance_bar_source,
        )

        return build_yfinance_bar_source(instrument=instrument)

    # Default: simulated bars.
    return SimulatedBarSource(
        instrument_id=instrument.id,
        # Derive the RNG seed from the account_id so each session
        # walks its own deterministic path.
        seed=abs(hash(str(account_id))) % (2**31),
    )


def _verifier_secret(request: Request) -> bytes:
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None or not hasattr(verifier, "secret"):
        raise RuntimeError("webapp:onboarding_secret_missing")
    secret = verifier.secret
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return secret


def _load_state(request: Request) -> WizardState:
    """Decode the signed ``wizard-state`` cookie; return the
    default state if absent or invalid."""
    cookie_value = request.cookies.get(WIZARD_COOKIE_NAME, "")
    if cookie_value:
        decoded = decode_state(cookie_value, secret=_verifier_secret(request))
        if decoded is not None:
            return decoded
    return WizardState()


def _render(
    request: Request,
    state: WizardState,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the onboarding template at the current step."""
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "onboarding.html",
        {
            "request": request,
            "state": state,
            "step": state.step,
            "error": error,
            "allowed_universes": ALLOWED_UNIVERSES,
            "allowed_strategies": ALLOWED_STRATEGIES,
            **fragment_context(request),
        },
        status_code=status_code,
    )


def _set_cookie(response, state: WizardState, secret: bytes) -> None:
    response.set_cookie(
        key=WIZARD_COOKIE_NAME,
        value=encode_state(state, secret=secret),
        httponly=True,
        samesite="lax",
        max_age=3600,  # 1h — the wizard is short-lived
    )


@router.get("", response_class=HTMLResponse)
async def get_onboarding(request: Request) -> HTMLResponse:
    """GET /onboarding — render the wizard.

    The step rendered is, in order:
    1. ``?step=<step1|step2|step3>`` query override (used by the
       "← Back" link to walk backwards without losing the saved
       inputs).
    2. The step persisted in the signed cookie (so refreshes
       resume).
    3. ``step1`` for a first-boot visit.

    Walking backwards persists the new step into the cookie so a
    further refresh stays on the chosen step.
    """
    state = _load_state(request)
    step_override = request.query_params.get("step", "").strip().lower()
    valid_steps = {"step1", "step2", "step3"}
    if step_override in valid_steps:
        new_state = WizardState(
            step=step_override,  # type: ignore[arg-type]
            starting_capital=state.starting_capital,
            universe=state.universe,
            strategy=state.strategy,
        )
        response = _render(request, new_state)
        _set_cookie(response, new_state, _verifier_secret(request))
        return response
    return _render(request, state)


@router.post("/step2", response_class=HTMLResponse)
async def post_step2(
    request: Request,
    starting_capital: str = Form(...),
    universe: str = Form(...),
    bar_source: str = Form("simulated"),
) -> HTMLResponse:
    """POST /onboarding/step2 — validate step 1, advance to step 2."""
    if not is_valid_capital(starting_capital):
        return _render(
            request,
            WizardState(starting_capital=starting_capital, universe=universe),
            error="webapp:onboarding:bad_capital",
            status_code=400,
        )
    if universe not in ALLOWED_UNIVERSES:
        return _render(
            request,
            WizardState(starting_capital=starting_capital, universe="eu-dividend-starter"),
            error="webapp:onboarding:bad_universe",
            status_code=400,
        )
    if bar_source not in ("simulated", "yfinance"):
        return _render(
            request,
            WizardState(starting_capital=starting_capital, universe=universe),
            error="webapp:onboarding:bad_bar_source",
            status_code=400,
        )
    new_state = WizardState(
        step="step2",
        starting_capital=starting_capital.strip(),
        universe=universe,
        strategy=_load_state(request).strategy,
        bar_source=bar_source,
    )
    response = _render(request, new_state)
    _set_cookie(response, new_state, _verifier_secret(request))
    return response


@router.post("/step3", response_class=HTMLResponse)
async def post_step3(
    request: Request,
    strategy: str = Form(...),
) -> HTMLResponse:
    """POST /onboarding/step3 — validate step 2, advance to step 3."""
    if strategy not in ALLOWED_STRATEGIES:
        return _render(
            request,
            WizardState(step="step2", strategy="CoreStrategy"),
            error="webapp:onboarding:bad_strategy",
            status_code=400,
        )
    current = _load_state(request)
    new_state = WizardState(
        step="step3",
        starting_capital=current.starting_capital,
        universe=current.universe,
        strategy=strategy,
        bar_source=current.bar_source,
    )
    response = _render(request, new_state)
    _set_cookie(response, new_state, _verifier_secret(request))
    return response


@router.post("/finish")
async def post_finish(request: Request) -> RedirectResponse:
    """POST /onboarding/finish — finalise the wizard.

    REQ_F_PAP_001 + REQ_F_PAP_004 + REQ_F_PAP_005 — construct a
    ticking ``PaperTradingRuntime`` and attach it to the shared
    ``RuntimeRegistry`` on ``app.state``. The session's
    ``account_id`` is fresh (``paper-<utc-iso-timestamp>``) so
    the registry's "one live session per account_id" invariant
    holds.

    Strategy wiring is intentionally skipped in v1 — the
    composed runtime ticks the broker + portfolio + equity
    series so the dashboard panel paints a live curve. Strategy
    + risk-engine gating land in a follow-up slice.
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from trading_system.models.money import Money
    from trading_system.models.phase import (
        AllocationBucket,
        MarketRegime,
        PhaseConstraints,
    )
    from trading_system.webapp.runtimes.paper_trading import (
        PaperTradingRuntime,
        build_runtime,
    )
    from trading_system.result import Err as ResultErr
    from trading_system.result import Ok as ResultOk

    state = _load_state(request)
    # Defensive capital re-validation — the cookie was signed +
    # already validated, but if anything went sideways we fall
    # back to the documented default rather than blow up.
    try:
        capital_amount = Decimal(state.starting_capital)
    except (ValueError, ArithmeticError):
        capital_amount = Decimal("10000")
    if capital_amount <= 0:
        capital_amount = Decimal("10000")

    account_id = new_paper_account_id()
    session = PaperTradingSession(
        account_id=account_id,
        universe=state.universe,
        strategy_id=StrategyId(state.strategy),
        starting_capital=Money(amount=capital_amount, currency=Currency.EUR),
        started_at=datetime.now(tz=UTC),
    )

    # Derive the natural phase from the starting capital + load
    # the matching constraints from config/phases.yaml. Defaults
    # to the Phase-1 fallback when the loader fails.
    from trading_system.webapp.runtimes.phase_loader import (
        phase_constraints_for_capital,
    )

    constraints = phase_constraints_for_capital(capital_amount)
    # Pull the actual first instrument from the universe YAML so
    # the wizard reflects the validated CAC 40 list (and any future
    # universes the operator adds) without code changes. The
    # hardcoded fallback covers the broken-YAML case.
    from trading_system.webapp.runtimes.universe_loader import (
        first_instrument_or_fallback,
    )

    instrument = first_instrument_or_fallback(
        state.universe, fallback=_DEFAULT_INSTRUMENTS[state.universe]
    )
    bar_source = _build_bar_source(
        state.bar_source, instrument=instrument, account_id=account_id
    )

    # Build the chosen strategy instance via the runtime-layer
    # factory (the views layer can't import strategies directly —
    # closed import graph).
    from trading_system.webapp.runtimes.strategy_factory import build_strategy

    strategy = build_strategy(
        state.strategy, strategy_id=StrategyId(state.strategy)
    )
    if strategy is None:
        return _render(  # type: ignore[return-value]
            request,
            state,
            error=f"webapp:onboarding:bad_strategy:{state.strategy}",
            status_code=400,
        )
    # The factory already wires the documented defaults for fee +
    # tax. The runtime falls back to TaxConfig.default() internally
    # when ``tax_config`` is None.

    runtime_result = build_runtime(
        session=session,
        instrument=instrument,
        strategy=strategy,
        bar_source=bar_source,
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
    )
    if isinstance(runtime_result, ResultErr):
        # Bubble back to step 1 with the categorised error
        # banner — the operator can fix the inputs and retry.
        response = _render(
            request,
            WizardState(),
            error=f"webapp:onboarding:runtime_failed:{runtime_result.error}",
            status_code=500,
        )
        return response  # type: ignore[return-value]
    assert isinstance(runtime_result, ResultOk)
    runtime: PaperTradingRuntime = runtime_result.value
    # Attach the simulated market-data provider so the strategy
    # step inside ``tick_once`` has bars to consult. Also set
    # the tax config so portfolio.apply has the rate.
    # Only the simulated source has a corresponding pure-Python
    # MarketDataProvider wrapper. The yfinance path wires the
    # provider directly (it already satisfies the Protocol).
    if state.bar_source == "simulated":
        runtime.market_data_provider = SimulatedMarketDataProvider(
            source=bar_source, instrument=instrument
        )
    else:
        runtime.market_data_provider = getattr(
            bar_source, "_provider", None
        )

    # Register against the shared registry so the dashboard
    # panel + tick driver both see the new session.
    registry = getattr(request.app.state, "runtime_registry", None)
    if registry is not None:
        start_result = registry.start(runtime)
        if isinstance(start_result, ResultErr):
            # Should not happen (fresh account_id) — render the
            # banner if it does so the operator isn't stuck on
            # an opaque 5xx.
            return _render(  # type: ignore[return-value]
                request,
                state,
                error=f"webapp:onboarding:register_failed:{start_result.error}",
                status_code=500,
            )
        # Drive one tick synchronously so the dashboard panel
        # paints a non-empty price + equity row on first load —
        # the operator otherwise stares at "—" for up to 2s
        # (the tick driver's cadence). Failure here is benign;
        # the driver retries on the next sweep.
        runtime.tick_once()

    from urllib.parse import quote as _urlquote

    # Log the session start into the operator inbox if wired.
    inbox = getattr(request.app.state, "notification_inbox", None)
    if inbox is not None and hasattr(inbox, "append"):
        from trading_system.webapp.inbox import InboxEntry

        try:
            inbox.append(
                InboxEntry(
                    at=datetime.now(tz=UTC),
                    category="paper-session",
                    code="session_started",
                    severity="info",
                    message=(
                        f"Paper session started · universe={state.universe} · "
                        f"strategy={state.strategy} · capital=€ {capital_amount}"
                    ),
                    account_id=str(account_id),
                )
            )
        except Exception:  # noqa: BLE001 — inbox failures stay non-fatal
            pass

    response = RedirectResponse(
        url=f"/?account_id={_urlquote(str(account_id), safe='')}",
        status_code=303,
    )
    response.delete_cookie(WIZARD_COOKIE_NAME)
    # Persist the freshly-created session as the "active" one so
    # the dashboard view falls back to it when the operator hits
    # ``/`` directly (or refreshes after closing the tab). The
    # cookie lifetime matches a typical operator session.
    response.set_cookie(
        key="active-paper-session",
        value=str(account_id),
        max_age=3600,
        samesite="lax",
        httponly=True,
    )
    # Short-lived breadcrumb the dashboard's JS uses to surface a
    # "session created" toast on first paint.
    response.set_cookie(
        key="paper-session-created",
        value=str(account_id),
        max_age=60,
        samesite="lax",
    )
    assert str(account_id).startswith(PAPER_ACCOUNT_PREFIX)
    return response


@router.post("/cancel")
async def post_cancel() -> RedirectResponse:
    """POST /onboarding/cancel — clear the wizard cookie + redirect to /."""
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(WIZARD_COOKIE_NAME)
    return response
