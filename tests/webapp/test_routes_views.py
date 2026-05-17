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


def test_dashboard_requires_household_token() -> None:
    client, _ = _client()
    response = client.get("/")
    assert response.status_code == 401


def test_dashboard_renders_html_with_hx_attributes() -> None:
    client, token = _client()
    response = client.get("/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # HTMX polling attributes — phase-A surface (Phase B replaces with sse).
    assert 'hx-get="/api/accounts/default/live-state"' in body
    assert "hx-trigger=" in body
    assert "hx-swap=" in body
    # Static htmx.min.js reference resolves through Starlette's url_for.
    assert "htmx.min.js" in body
    # No external CDN imports — REQ_SDS_FAS_003 / REQ_SDD_FAS_003 spirit.
    assert "https://unpkg.com" not in body
    assert "https://cdn." not in body


def test_dashboard_renders_account_id_in_chrome() -> None:
    client, token = _client()
    response = client.get("/", headers={"Authorization": f"Bearer {token}"})
    assert "default" in response.text
