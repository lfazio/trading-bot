"""Tests for the strategy registry panel (REQ_F_WEB2_006)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.result import Err, Ok
from trading_system.webapp import WebappState, create_app


_SECRET = b"strategies-view-secret"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakeRegistry:
    strategies: list[dict[str, object]] = field(default_factory=list)

    def list_strategies(self) -> list[dict[str, object]]:
        return list(self.strategies)


@dataclass(slots=True)
class _FakePromoter:
    accept: bool = True
    reason: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)

    def promote(
        self,
        *,
        strategy_id,
        operator_token,
        operator_id,
        rationale,
        account_id,
    ):
        self.calls.append(
            {
                "strategy_id": str(strategy_id),
                "operator_token": operator_token,
                "operator_id": operator_id,
                "rationale": rationale,
                "account_id": str(account_id),
            }
        )
        if self.accept:
            return Ok(None)
        return Err(self.reason)


def _client(*, reader=None, promoter=None):
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    state = WebappState(
        token_verifier=verifier,
        strategy_registry_reader=reader,
        registry_promoter=promoter,
    )
    return TestClient(create_app(state)), verifier


def _token(verifier):
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC))


# ---------------------------------------------------------------------------
# GET — list rendering
# ---------------------------------------------------------------------------


def test_strategies_redirects_unauth_to_login() -> None:
    client, _ = _client(reader=_FakeRegistry())
    response = client.get("/strategies", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_strategies_renders_empty_state_when_no_reader_wired() -> None:
    client, verifier = _client(reader=None)
    response = client.get(
        "/strategies", headers={"Authorization": f"Bearer {_token(verifier)}"}
    )
    assert response.status_code == 200
    body = response.text
    assert "No strategies registered" in body
    # No promoter -> warn banner.
    assert "registry_promoter" in body


def test_strategies_renders_each_row_with_status_badge() -> None:
    reader = _FakeRegistry(
        strategies=[
            {
                "id": "core_v1",
                "status": "validated",
                "last_promoted_at": "2026-05-01T12:00:00+00:00",
                "improvement_report": "OOS Sharpe 1.4",
            },
            {
                "id": "tactical_v2",
                "status": "experimental",
                "improvement_report": "trend signal, OOS Sharpe 0.9",
            },
        ]
    )
    client, verifier = _client(reader=reader, promoter=_FakePromoter())
    body = client.get(
        "/strategies", headers={"Authorization": f"Bearer {_token(verifier)}"}
    ).text
    assert "core_v1" in body
    assert "tactical_v2" in body
    # Status badges (audited by REQ_SDD_WEB2_009 — carry aria-label).
    assert 'aria-label="Strategy status validated"' in body
    assert 'aria-label="Strategy status experimental"' in body
    # Improvement report surfaced.
    assert "OOS Sharpe 1.4" in body
    # Promote affordance ONLY on the experimental row.
    assert body.count("Submit promotion") == 1


def test_static_strategy_registry_reader_lists_documented_demos() -> None:
    """The demo deploy SHALL wire StaticStrategyRegistryReader so
    the /strategies page lists the strategies the wizard's factory
    dispatches on (CoreStrategy + TacticalStrategy) instead of
    showing the empty-state placeholder."""
    from trading_system.webapp.strategy_registry_reader import (
        StaticStrategyRegistryReader,
    )

    reader = StaticStrategyRegistryReader()
    strategies = reader.list_strategies()
    ids = {s["id"] for s in strategies}
    assert ids == {"CoreStrategy", "TacticalStrategy"}
    # Both ship at "validated" status (they're the documented
    # production strategies, not experimental hypotheses).
    for s in strategies:
        assert s["status"] == "validated"
        assert s["improvement_report"]  # non-empty blurb


def test_strategies_hides_promote_when_no_promoter_wired() -> None:
    reader = _FakeRegistry(
        strategies=[
            {"id": "x_v1", "status": "experimental", "improvement_report": "..."}
        ]
    )
    client, verifier = _client(reader=reader, promoter=None)
    body = client.get(
        "/strategies", headers={"Authorization": f"Bearer {_token(verifier)}"}
    ).text
    # Warn banner mentions the missing promoter.
    assert "registry_promoter" in body
    # Promote button NOT rendered.
    assert "Submit promotion" not in body


# ---------------------------------------------------------------------------
# POST — form-encoded promote
# ---------------------------------------------------------------------------


def test_promote_redirects_unauth_to_login() -> None:
    client, _ = _client(reader=_FakeRegistry(), promoter=_FakePromoter())
    response = client.post(
        "/strategies/x_v1/promote",
        data={
            "operator_token": "tok",
            "operator_id": "alice",
            "rationale": "OK",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_promote_invokes_promoter_with_form_args() -> None:
    promoter = _FakePromoter(accept=True)
    client, verifier = _client(
        reader=_FakeRegistry(), promoter=promoter
    )
    response = client.post(
        "/strategies/core_v1/promote",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "operator_token": "hmac-token",
            "operator_id": "alice",
            "rationale": "OOS Sharpe 1.4 across 3 walk-forward windows",
            "account_id": "default",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/strategies"
    # Promoter called with the form args.
    assert len(promoter.calls) == 1
    call = promoter.calls[0]
    assert call["strategy_id"] == "core_v1"
    assert call["operator_token"] == "hmac-token"
    assert call["operator_id"] == "alice"
    assert call["account_id"] == "default"
    # Success flash cookie set.
    cookies = response.headers.get_list("set-cookie")
    assert any("strategies-flash=registry:promoted:core_v1" in h for h in cookies)


def test_promote_surfaces_err_via_flash_cookie() -> None:
    promoter = _FakePromoter(accept=False, reason="registry:token_invalid")
    client, verifier = _client(reader=_FakeRegistry(), promoter=promoter)
    response = client.post(
        "/strategies/core_v1/promote",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "operator_token": "bad",
            "operator_id": "alice",
            "rationale": "...",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookies = response.headers.get_list("set-cookie")
    assert any(
        "strategies-flash=registry:token_invalid" in h for h in cookies
    )


def test_promote_503_when_no_promoter_wired() -> None:
    """No promoter -> redirect with a webapp:registry_promoter_missing
    flash so the operator sees what's wrong on the next page render."""
    client, verifier = _client(reader=_FakeRegistry(), promoter=None)
    response = client.post(
        "/strategies/core_v1/promote",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "operator_token": "tok",
            "operator_id": "alice",
            "rationale": "...",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookies = response.headers.get_list("set-cookie")
    assert any(
        "strategies-flash=webapp:registry_promoter_missing" in h for h in cookies
    )


def test_flash_cookie_cleared_after_render() -> None:
    """The flash cookie SHALL be consumed (cleared) once the
    next /strategies page renders so it doesn't persist across
    refreshes."""
    promoter = _FakePromoter(accept=True)
    client, verifier = _client(reader=_FakeRegistry(), promoter=promoter)
    # Trigger a promotion -> sets the flash.
    client.post(
        "/strategies/core_v1/promote",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "operator_token": "tok",
            "operator_id": "alice",
            "rationale": "...",
        },
        follow_redirects=False,
    )
    # Render -> shows the banner + clears the cookie.
    render_response = client.get(
        "/strategies", headers={"Authorization": f"Bearer {_token(verifier)}"}
    )
    body = render_response.text
    assert "Promotion accepted for" in body
    # The render handler emits a delete_cookie header.
    cookies = render_response.headers.get_list("set-cookie")
    assert any(
        "strategies-flash=" in h and ("Max-Age=0" in h or "expires" in h.lower())
        for h in cookies
    )
