"""Tests for the paper-session stop control (REQ_F_WEB2_003)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId
from trading_system.result import Nothing, Some
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.runtimes.paper_trading import RuntimeRegistry


_SECRET = b"stop-test-secret"


def _make_client_with_runtime_registered():
    """Walk the wizard to land a registered runtime + return the
    test client, the verifier, the registry, and the new account_id."""
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    registry = RuntimeRegistry()
    state = WebappState(
        token_verifier=verifier,
        runtime_registry=registry,
    )
    client = TestClient(create_app(state))
    client.post(
        "/onboarding/step2",
        data={"starting_capital": "10000", "universe": "eu-dividend-starter"},
    )
    client.post("/onboarding/step3", data={"strategy": "CoreStrategy"})
    response = client.post("/onboarding/finish", follow_redirects=False)
    from urllib.parse import unquote

    aid = AccountId(unquote(response.headers["location"].split("account_id=", 1)[1]))
    return client, verifier, registry, aid


def test_stop_route_requires_auth() -> None:
    client, _, _, aid = _make_client_with_runtime_registered()
    # The TestClient persists the active-paper-session cookie from
    # the wizard's finish. Clear cookies to test the auth gate.
    client.cookies.clear()
    response = client.post(
        f"/paper-sessions/{aid}/stop", follow_redirects=False
    )
    assert response.status_code == 401


def test_stop_route_de_registers_runtime_and_clears_cookie() -> None:
    client, verifier, registry, aid = _make_client_with_runtime_registered()
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC)
    )
    assert isinstance(registry.status(aid), Some)
    response = client.post(
        f"/paper-sessions/{aid}/stop",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # Runtime is gone from the registry.
    assert isinstance(registry.status(aid), Nothing)
    # The active-paper-session cookie is cleared so the dashboard
    # doesn't keep falling back to the dead session.
    cookies_header = response.headers.get_list("set-cookie")
    assert any(
        "active-paper-session" in h
        and ("Max-Age=0" in h or "expires" in h.lower())
        for h in cookies_header
    )


def test_stop_unknown_session_is_idempotent_no_op() -> None:
    """Stopping an account_id that isn't live SHALL still return
    303 -> / (the operator might have refreshed twice)."""
    client, verifier, _, _ = _make_client_with_runtime_registered()
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC)
    )
    response = client.post(
        "/paper-sessions/paper-not-a-real-session/stop",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_stop_keeps_cookie_when_stopping_a_different_session() -> None:
    """If the operator stops session A while session B is the
    currently-active one in the cookie, the cookie SHALL stay
    intact (the dashboard still falls back to B)."""
    client, verifier, registry, aid_a = _make_client_with_runtime_registered()
    # Walk the wizard again to create + activate session B.
    client.post(
        "/onboarding/step2",
        data={"starting_capital": "20000", "universe": "cac40"},
    )
    client.post("/onboarding/step3", data={"strategy": "TacticalStrategy"})
    response = client.post("/onboarding/finish", follow_redirects=False)
    from urllib.parse import unquote

    aid_b = AccountId(
        unquote(response.headers["location"].split("account_id=", 1)[1])
    )
    # The cookie now points at session B (the wizard always
    # overwrites it on success).
    assert aid_a != aid_b
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC)
    )
    stop_response = client.post(
        f"/paper-sessions/{aid_a}/stop",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert stop_response.status_code == 303
    # active-paper-session cookie was NOT cleared (still points at B).
    cookies_header = stop_response.headers.get_list("set-cookie")
    assert not any(
        "active-paper-session" in h
        and ("Max-Age=0" in h or "expires" in h.lower())
        for h in cookies_header
    )
    # Session B is still in the registry; session A is gone.
    assert isinstance(registry.status(aid_a), Nothing)
    assert isinstance(registry.status(aid_b), Some)


def test_dashboard_renders_stop_button_only_when_session_is_live() -> None:
    client, verifier, _, aid = _make_client_with_runtime_registered()
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC)
    )
    response = client.get(
        f"/?account_id={aid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert f"/paper-sessions/{aid}" in response.text or "stop" in response.text.lower()
    # And on a no-session view the button SHALL NOT appear.
    response_default = client.get(
        "/?account_id=default",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert "/paper-sessions/default/stop" not in response_default.text
