"""Tests for the CR-019 onboarding wizard.

REQ refs:
- REQ_F_WEB2_001 — 3-step wizard renders + advances + finishes.
- REQ_SDD_WEB2_002 — wizard state lives in an HMAC-signed
  ``wizard-state`` httponly cookie; cancel clears it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.runtimes.paper_trading import (
    PAPER_ACCOUNT_PREFIX,
    RuntimeRegistry,
)
from trading_system.webapp.wizard_state import (
    ALLOWED_STRATEGIES,
    ALLOWED_UNIVERSES,
    WIZARD_COOKIE_NAME,
    WizardState,
    decode_state,
    encode_state,
)


# ---------------------------------------------------------------------------
# wizard_state — unit tests
# ---------------------------------------------------------------------------


_SECRET = b"wizard-test-secret"


def test_encode_then_decode_round_trips() -> None:
    state = WizardState(
        step="step2",
        starting_capital="12345.67",
        universe="cac40",
        strategy="TacticalStrategy",
    )
    cookie = encode_state(state, secret=_SECRET)
    assert "." in cookie
    decoded = decode_state(cookie, secret=_SECRET)
    assert decoded == state


def test_encode_is_deterministic_for_replay() -> None:
    state = WizardState()
    a = encode_state(state, secret=_SECRET)
    b = encode_state(state, secret=_SECRET)
    assert a == b


def test_decode_rejects_tampered_signature() -> None:
    state = WizardState()
    cookie = encode_state(state, secret=_SECRET)
    body, _, sig = cookie.rpartition(".")
    tampered = f"{body}.{'a' * len(sig)}"
    assert decode_state(tampered, secret=_SECRET) is None


def test_decode_rejects_unknown_universe() -> None:
    state_dict = {
        "step": "step1",
        "starting_capital": "10000",
        "universe": "not-a-universe",
        "strategy": "CoreStrategy",
    }
    import base64
    import hashlib
    import hmac
    import json

    canonical = json.dumps(state_dict, sort_keys=True, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(canonical).rstrip(b"=").decode()
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    cookie = f"{body}.{sig}"
    assert decode_state(cookie, secret=_SECRET) is None


def test_decode_returns_none_for_missing_dot() -> None:
    assert decode_state("no-dot-here", secret=_SECRET) is None
    assert decode_state("", secret=_SECRET) is None


def test_decode_returns_none_for_bad_b64() -> None:
    cookie = "!!notb64!!.deadbeef"
    assert decode_state(cookie, secret=_SECRET) is None


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


def _make_client() -> TestClient:
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    registry = RuntimeRegistry()
    state = WebappState(
        token_verifier=verifier,
        runtime_registry=registry,
    )
    return TestClient(create_app(state))


def test_get_onboarding_renders_step1() -> None:
    client = _make_client()
    response = client.get("/onboarding")
    assert response.status_code == 200
    body = response.text
    assert "Step 1" in body
    assert 'name="starting_capital"' in body
    assert 'name="universe"' in body
    # Modal accessibility attributes.
    assert 'role="dialog"' in body
    assert 'aria-modal="true"' in body


def test_post_step2_advances_and_sets_signed_cookie() -> None:
    client = _make_client()
    response = client.post(
        "/onboarding/step2",
        data={"starting_capital": "10000", "universe": "cac40"},
    )
    assert response.status_code == 200
    # The render switches to the strategy form.
    assert "Step 2" in response.text
    assert 'name="strategy"' in response.text
    # Cookie was set + signed.
    cookie = response.cookies.get(WIZARD_COOKIE_NAME)
    assert cookie is not None
    decoded = decode_state(cookie, secret=_SECRET)
    assert decoded is not None
    assert decoded.step == "step2"
    assert decoded.universe == "cac40"
    assert decoded.starting_capital == "10000"


def test_post_step2_rejects_bad_capital() -> None:
    client = _make_client()
    response = client.post(
        "/onboarding/step2",
        data={"starting_capital": "-100", "universe": "eu-dividend-starter"},
    )
    assert response.status_code == 400
    assert "Starting capital must be a positive decimal number" in response.text


def test_post_step2_rejects_unknown_universe() -> None:
    client = _make_client()
    response = client.post(
        "/onboarding/step2",
        data={"starting_capital": "10000", "universe": "made-up"},
    )
    assert response.status_code == 400
    assert "Unknown universe selection" in response.text


def test_post_step3_advances_to_confirm() -> None:
    client = _make_client()
    # Step 1 → step 2.
    client.post(
        "/onboarding/step2",
        data={"starting_capital": "10000", "universe": "cac40"},
    )
    # Step 2 → step 3 (the TestClient persists cookies between calls).
    response = client.post(
        "/onboarding/step3", data={"strategy": "TacticalStrategy"}
    )
    assert response.status_code == 200
    assert "Step 3" in response.text
    # Carries forward the prior choices.
    assert "cac40" in response.text
    assert "TacticalStrategy" in response.text


def test_post_step3_rejects_unknown_strategy() -> None:
    client = _make_client()
    response = client.post(
        "/onboarding/step3", data={"strategy": "MoonShotStrategy"}
    )
    assert response.status_code == 400
    assert "Unknown strategy selection" in response.text


def test_post_finish_redirects_and_clears_cookie() -> None:
    client = _make_client()
    # Walk steps 1 → 2 → 3 → finish.
    client.post(
        "/onboarding/step2",
        data={"starting_capital": "12345.67", "universe": "eu-dividend-starter"},
    )
    client.post("/onboarding/step3", data={"strategy": "CoreStrategy"})
    # `httpx`'s TestClient follows 303 by default — disable so we
    # can introspect the redirect target + cookie state.
    response = client.post("/onboarding/finish", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/?account_id=")
    # The wizard-state cookie SHALL be cleared (delete_cookie
    # emits an expiring Set-Cookie header).
    cookies_header = response.headers.get_list("set-cookie")
    assert any(
        WIZARD_COOKIE_NAME in h and ("Max-Age=0" in h or "expires" in h.lower())
        for h in cookies_header
    )
    # The newly-issued account_id starts with the paper- prefix
    # (decode after unquoting — colons + plus get percent-encoded
    # so the browser doesn't mis-decode the ISO timestamp).
    from urllib.parse import unquote

    aid = unquote(location.split("account_id=", 1)[1])
    assert aid.startswith(PAPER_ACCOUNT_PREFIX)
    # A short-lived breadcrumb cookie was set.
    assert any("paper-session-created=" in h for h in cookies_header)


def test_post_cancel_clears_cookie_and_redirects_home() -> None:
    client = _make_client()
    client.post(
        "/onboarding/step2",
        data={"starting_capital": "10000", "universe": "cac40"},
    )
    response = client.post("/onboarding/cancel", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookies_header = response.headers.get_list("set-cookie")
    assert any(
        WIZARD_COOKIE_NAME in h and ("Max-Age=0" in h or "expires" in h.lower())
        for h in cookies_header
    )


def test_finish_then_paper_state_endpoint_reports_live() -> None:
    """End-to-end: walk the wizard, then fetch the paper-state
    endpoint with a Bearer token — the response SHALL report
    ``is_alive=true`` for the freshly-registered session."""
    import json

    from trading_system.accounts.token_verifier import HOUSEHOLD_CLAIM
    from trading_system.webapp.paper_state_reader import (
        RuntimePaperStateReader,
    )

    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    registry = RuntimeRegistry()
    reader = RuntimePaperStateReader(registry=registry)
    state = WebappState(
        token_verifier=verifier,
        runtime_registry=registry,
        paper_state_reader=reader,
    )
    app = create_app(state)
    client = TestClient(app)
    client.post(
        "/onboarding/step2",
        data={"starting_capital": "10000", "universe": "eu-dividend-starter"},
    )
    client.post("/onboarding/step3", data={"strategy": "CoreStrategy"})
    response = client.post("/onboarding/finish", follow_redirects=False)
    assert response.status_code == 303
    from urllib.parse import unquote

    aid = unquote(response.headers["location"].split("account_id=", 1)[1])
    # Issue a household token (the dashboard's view consumes
    # this — reuse the same scope for the paper-state endpoint).
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM,
        now=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    state_response = client.get(
        f"/api/accounts/{aid}/paper-state",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert state_response.status_code == 200
    body = json.loads(state_response.content)
    assert body["account_id"] == aid
    assert body["is_alive"] is True
    assert body["is_degraded"] is False


def test_finish_registers_a_live_runtime_in_the_shared_registry() -> None:
    """REQ_F_PAP_001 + REQ_F_PAP_005 — the finish handler SHALL
    actually attach a ticking ``PaperTradingRuntime`` to the
    shared ``RuntimeRegistry`` so the dashboard panel paints
    live equity ticks immediately."""
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    registry = RuntimeRegistry()
    state = WebappState(
        token_verifier=verifier,
        runtime_registry=registry,
    )
    app = create_app(state)
    client = TestClient(app)
    client.post(
        "/onboarding/step2",
        data={"starting_capital": "12345.67", "universe": "eu-dividend-starter"},
    )
    client.post("/onboarding/step3", data={"strategy": "CoreStrategy"})
    response = client.post("/onboarding/finish", follow_redirects=False)
    assert response.status_code == 303
    # The registry SHALL hold the newly-registered runtime.
    live_ids = registry.live_account_ids()
    assert len(live_ids) == 1
    aid = live_ids[0]
    assert str(aid).startswith(PAPER_ACCOUNT_PREFIX)
    # Drive one tick manually so the equity-history sanity check
    # is not depending on the asyncio lifespan task. (The
    # production lifespan starts the PaperTickDriver
    # automatically.)
    from trading_system.result import Some

    opt = registry.status(aid)
    assert isinstance(opt, Some)
    runtime = opt.value
    runtime.tick_once().unwrap()
    # The finish handler drives one synchronous tick before
    # returning so the dashboard panel paints immediately; the
    # manual tick above adds a second.
    assert len(runtime.equity_history()) >= 1


def test_allowed_universes_and_strategies_are_closed_sets() -> None:
    """Adding a new universe / strategy SHALL be a deliberate
    code change here + a corresponding wiki amendment, not a
    cookie-driven side-effect."""
    assert ALLOWED_UNIVERSES == ("eu-dividend-starter", "cac40")
    assert ALLOWED_STRATEGIES == ("CoreStrategy", "TacticalStrategy")
