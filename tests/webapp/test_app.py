"""TC_FAS_001 — ``create_app`` route inventory + basic boot.

REQ refs: REQ_F_FAS_001, REQ_F_FAS_004, REQ_SDS_FAS_001.
"""

from __future__ import annotations

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.webapp import WebappState, create_app


def _state() -> WebappState:
    verifier = AccountScopedTokenVerifier(
        secret=b"test-secret", ttl_seconds=3600
    )
    return WebappState(token_verifier=verifier)


def test_create_app_returns_fastapi_with_documented_routes() -> None:
    app = create_app(_state())
    # Collect routes with HTTP methods only — the static mount + the
    # auto-generated /openapi.json show up too but the documented
    # endpoint inventory is the API + view set.
    paths = {r.path for r in app.routes if hasattr(r, "methods") and r.methods}
    expected_subset = {
        "/health",
        "/api/accounts/{account_id}/live-state",
        "/api/registry/{strategy_id}/promote",
        "/",
        "/openapi.json",
        "/docs",
        "/redoc",
    }
    missing = expected_subset - paths
    assert not missing, f"missing routes: {missing}"


def test_openapi_schema_includes_all_phase_a_endpoints() -> None:
    """REQ_F_FAS_004 — every Phase A endpoint SHALL appear in the
    OpenAPI schema."""
    app = create_app(_state())
    schema = app.openapi()
    paths = set(schema["paths"].keys())
    assert "/health" in paths
    assert "/api/accounts/{account_id}/live-state" in paths
    assert "/api/registry/{strategy_id}/promote" in paths


def test_static_mount_serves_htmx_placeholder() -> None:
    """The bundled ``htmx.min.js`` placeholder SHALL be reachable
    via ``GET /static/htmx.min.js`` so the dashboard template's
    ``url_for('static', path='htmx.min.js')`` resolves at runtime."""
    from fastapi.testclient import TestClient

    app = create_app(_state())
    client = TestClient(app)
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    # Placeholder content includes the documented marker.
    assert "htmx" in response.text.lower()


def test_health_endpoint_reachable_without_auth() -> None:
    from fastapi.testclient import TestClient

    app = create_app(_state())
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    # Canonical JSON ⇒ keys sorted alphabetically.
    text = response.text
    assert text.index('"as_of"') < text.index('"status"')
    assert text.index('"status"') < text.index('"version"')


def test_default_app_requires_operator_secret_env(monkeypatch) -> None:
    """``default_app()`` fail-fasts when the operator secret env var
    is unset — REQ_F_FAS_005 hardening."""
    import pytest

    monkeypatch.delenv("TRADING_BOT_OPERATOR_SECRET", raising=False)
    from trading_system.webapp.app import default_app

    with pytest.raises(RuntimeError, match="webapp:missing_operator_secret"):
        default_app()


def test_default_app_constructs_when_env_set(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "test-secret")
    from trading_system.webapp.app import default_app

    app = default_app()
    assert app is not None
    # The token verifier got wired through.
    assert app.state.token_verifier is not None
