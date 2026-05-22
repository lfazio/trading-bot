"""TC_FAS_004 (subset) — HTMX dashboard renders without SSE.

Phase A's dashboard uses HTMX `hx-get` polling against the live-state
endpoint; the SSE channel + `hx-ext="sse"` attribute land in Phase B.
This test verifies the page renders + carries the documented HTMX
attributes for the polling path.

REQ refs: REQ_F_FAS_002, REQ_SDS_FAS_003.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app


def _client() -> tuple[TestClient, str]:
    verifier = AccountScopedTokenVerifier(secret=b"phase-a-secret", ttl_seconds=3600)
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))
    app = create_app(WebappState(token_verifier=verifier))
    return TestClient(app), token


def test_dashboard_redirects_to_login_when_unauth() -> None:
    """Phase-B browser path — the HTML entry point redirects to
    /login instead of returning raw 401 JSON. Tooling paths still
    get JSON 401s on the API endpoints."""
    client, _ = _client()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_renders_html_with_sse_wiring() -> None:
    client, token = _client()
    response = client.get("/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # SSE surface — the panel mounts an EventSource targeting the
    # push channel (the bundled HTMX stub doesn't carry the real
    # runtime, so the dashboard wires the connection natively).
    assert "data-live-sse-url=" in body
    assert "/events/live-state" in body
    assert "EventSource" in body
    # Static asset references resolve through Starlette's url_for.
    assert "htmx.min.js" in body
    assert "htmx-sse.min.js" in body
    # No external CDN imports — REQ_SDS_FAS_003 / REQ_SDD_FAS_003 spirit.
    assert "https://unpkg.com" not in body
    assert "https://cdn." not in body


def test_dashboard_renders_account_id_in_chrome() -> None:
    client, token = _client()
    response = client.get("/", headers={"Authorization": f"Bearer {token}"})
    assert "default" in response.text
