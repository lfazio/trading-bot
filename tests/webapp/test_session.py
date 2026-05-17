"""Cookie-session auth (Phase B step 4).

REQ refs:
- REQ_F_FAS_005 — both Bearer header AND HTTP-only cookie accepted.
- REQ_SDD_FAS_004 — cookie is ``HttpOnly`` + ``SameSite=Strict``;
  raw operator token never persisted server-side.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.auth_deps import SESSION_COOKIE_NAME


def _client_with_token(*, ttl: int = 3600) -> tuple[TestClient, str, AccountScopedTokenVerifier]:
    verifier = AccountScopedTokenVerifier(secret=b"phase-b-secret", ttl_seconds=ttl)
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))
    app = create_app(WebappState(token_verifier=verifier))
    return TestClient(app), token, verifier


# ---------------------------------------------------------------------------
# POST /api/session
# ---------------------------------------------------------------------------


def test_open_session_sets_secure_cookie() -> None:
    client, token, _ = _client_with_token()
    response = client.post(
        "/api/session",
        json={"token": token, "account_id": HOUSEHOLD_CLAIM},
    )
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie or "SameSite=Strict" in set_cookie


def test_open_session_rejects_bad_token() -> None:
    client, _, _ = _client_with_token()
    response = client.post(
        "/api/session",
        json={"token": "not-a-valid-token", "account_id": HOUSEHOLD_CLAIM},
    )
    assert response.status_code == 401
    assert b"registry:token_invalid" in response.content


def test_cookie_session_authorises_subsequent_get() -> None:
    """After ``POST /api/session`` the session cookie is enough — no
    Authorization header needed on the next request."""
    client, token, _ = _client_with_token()
    client.post(
        "/api/session",
        json={"token": token, "account_id": HOUSEHOLD_CLAIM},
    )
    # TestClient persists cookies between calls.
    response = client.get("/api/accounts/default/live-state")
    # The live-state route needs a reader; we didn't wire one, so
    # the auth check passes but the route fail-fasts with 500.
    # Either way: a 401 here would mean the cookie wasn't honoured.
    assert response.status_code != 401, (
        f"cookie auth not honoured; got {response.status_code} body={response.content!r}"
    )


def test_close_session_clears_cookie() -> None:
    client, token, _ = _client_with_token()
    client.post(
        "/api/session",
        json={"token": token, "account_id": HOUSEHOLD_CLAIM},
    )
    response = client.delete("/api/session")
    assert response.status_code == 200
    # The delete sets an expired cookie to clear the browser-side.
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------


def test_login_page_renders_without_auth() -> None:
    client, _, _ = _client_with_token()
    response = client.get("/login")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # The form posts to /api/session.
    assert "/api/session" in body
    assert "operator token" in body.lower()


# ---------------------------------------------------------------------------
# Dashboard graceful redirect
# ---------------------------------------------------------------------------


def test_dashboard_redirects_to_login_when_unauth() -> None:
    """REQ_F_FAS_002 spirit — the HTML entry point is browser-friendly
    and redirects to /login instead of returning a raw 401 JSON."""
    client, _, _ = _client_with_token()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_accepts_cookie_session() -> None:
    client, token, _ = _client_with_token()
    client.post(
        "/api/session",
        json={"token": token, "account_id": HOUSEHOLD_CLAIM},
    )
    response = client.get("/")
    assert response.status_code == 200
    assert "trading-bot dashboard" in response.text
