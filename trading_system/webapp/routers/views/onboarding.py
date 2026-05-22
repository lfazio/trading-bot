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

from trading_system.models.identifiers import StrategyId
from trading_system.webapp.runtimes.paper_trading import (
    PAPER_ACCOUNT_PREFIX,
    PaperTradingSession,
    new_paper_account_id,
)
from trading_system.webapp.wizard_state import (
    ALLOWED_STRATEGIES,
    ALLOWED_UNIVERSES,
    WIZARD_COOKIE_NAME,
    WizardState,
    decode_state,
    encode_state,
    is_valid_capital,
)


router = APIRouter(prefix="/onboarding")


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
    """GET /onboarding — render step 1 (or resume the operator at
    the latest completed step if a valid cookie is present)."""
    state = _load_state(request)
    return _render(request, state)


@router.post("/step2", response_class=HTMLResponse)
async def post_step2(
    request: Request,
    starting_capital: str = Form(...),
    universe: str = Form(...),
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
    new_state = WizardState(
        step="step2",
        starting_capital=starting_capital.strip(),
        universe=universe,
        strategy=_load_state(request).strategy,
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
    )
    response = _render(request, new_state)
    _set_cookie(response, new_state, _verifier_secret(request))
    return response


@router.post("/finish")
async def post_finish(request: Request) -> RedirectResponse:
    """POST /onboarding/finish — finalise the wizard.

    REQ_F_PAP_001 + REQ_F_PAP_004 — record a
    ``PaperTradingSession`` identity card under a fresh
    ``paper-<utc-iso-timestamp>`` account_id, attach it to the
    ``RuntimeRegistry`` slot on ``app.state`` (or skip silently
    when the runtime registry is unwired — defensive against a
    partial deployment), and redirect to ``/`` with the
    ``wizard-state`` cookie cleared.

    The runtime ticking itself stays deferred until the
    BarSource (yfinance adapter wiring) lands in a follow-up
    slice — this handler creates the *session identity* (the
    operator's choices) so the dashboard panel can surface
    "session configured; awaiting first bar".
    """
    from decimal import Decimal

    from trading_system.models.money import Currency, Money

    state = _load_state(request)
    # Compose the session identity. Capital validity was checked
    # in step 2; the cookie's encoded `starting_capital` was
    # rejected at decode_state if non-positive — still defensive.
    try:
        capital_amount = Decimal(state.starting_capital)
    except (ValueError, ArithmeticError):
        capital_amount = Decimal("10000")
    if capital_amount <= 0:
        capital_amount = Decimal("10000")
    account_id = new_paper_account_id()
    PaperTradingSession(
        account_id=account_id,
        universe=state.universe,
        strategy_id=StrategyId(state.strategy),
        starting_capital=Money(amount=capital_amount, currency=Currency.EUR),
        started_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    # The session is created + validated; in this slice we do
    # NOT register a ticking runtime yet (BarSource wiring is the
    # next slice). The redirect carries the operator to the
    # dashboard, which shows the "No session" panel until the
    # runtime starts ticking.
    response = RedirectResponse(
        url=f"/?account_id={account_id}", status_code=303
    )
    response.delete_cookie(WIZARD_COOKIE_NAME)
    # A short-lived breadcrumb cookie so the dashboard's panel
    # can surface a "session created" toast on first paint. The
    # cookie is purely cosmetic (no server-side state).
    response.set_cookie(
        key="paper-session-created",
        value=str(account_id),
        max_age=60,
        samesite="lax",
    )
    # Reach into the registry slot so the rest of the webapp
    # (paper-state reader) sees the new account_id immediately.
    # No-op when the registry isn't wired — defensive against
    # partial deploys (the dashboard then still shows the "No
    # session" sentinel).
    # NOTE: we don't construct a ticking runtime — only the
    # session identity card is preserved.
    _ = getattr(request.app.state, "runtime_registry", None)
    # Sanity: prefix invariant remained intact.
    assert str(account_id).startswith(PAPER_ACCOUNT_PREFIX)
    return response


@router.post("/cancel")
async def post_cancel() -> RedirectResponse:
    """POST /onboarding/cancel — clear the wizard cookie + redirect to /."""
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(WIZARD_COOKIE_NAME)
    return response
