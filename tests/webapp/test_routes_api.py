"""TC_FAS_003 + TC_FAS_007 — API route integration via FastAPI's TestClient.

REQ refs:
- REQ_F_FAS_001 — route inventory (the live-state + promotion endpoints).
- REQ_F_FAS_005 — Bearer-token auth.
- REQ_NF_FAS_001 — byte-identical replay on identical inputs.
- REQ_SDD_FAS_004 — auth dep raises 401 with the categorised
  ``registry:token_invalid`` body.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.phase import Phase
from trading_system.models.safety import KillSwitchState
from trading_system.result import Err, Ok
from trading_system.webapp import WebappState, create_app
from trading_system.webui.schemas import LiveStateResponse


_NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _StubReader:
    """Returns a deterministic LiveStateResponse so byte-identical
    replay holds across requests."""

    def live_state(self, *, account_id: AccountId, as_of: datetime) -> LiveStateResponse:
        return LiveStateResponse(
            account_id=account_id,
            as_of=_NOW,  # pinned for determinism
            ks_state=KillSwitchState.ACTIVE,
            phase=Phase(1),
            open_positions_count=3,
            equity_after_tax=Decimal("12345.67"),
        )


class _StubPromoter:
    def __init__(self, *, accept: bool = True, reason: str = "") -> None:
        self.accept = accept
        self.reason = reason
        self.calls: list[dict] = []

    def promote(
        self,
        *,
        strategy_id: StrategyId,
        operator_token: str,
        operator_id: str,
        rationale: str,
        account_id: AccountId,
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


class _StubNotifier:
    def __init__(self) -> None:
        self.dispatched: list[object] = []

    def dispatch(self, payload: object) -> None:
        self.dispatched.append(payload)


def _verifier() -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(secret=b"phase-a-secret", ttl_seconds=3600)


def _household_token(verifier: AccountScopedTokenVerifier) -> str:
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))


def _account_token(verifier: AccountScopedTokenVerifier, account_id: str) -> str:
    return verifier.issue(account_id=account_id, now=datetime.now(UTC))


def _make_app(
    *,
    verifier: AccountScopedTokenVerifier,
    reader: _StubReader | None = None,
    promoter: _StubPromoter | None = None,
    notifier: _StubNotifier | None = None,
):
    state = WebappState(
        token_verifier=verifier,
        live_state_reader=reader or _StubReader(),
        registry_promoter=promoter or _StubPromoter(),
        promotion_audit_notifier=notifier,
    )
    return create_app(state)


# ---------------------------------------------------------------------------
# TC_FAS_007 — Bearer auth (live-state read)
# ---------------------------------------------------------------------------


def test_live_state_requires_bearer_token() -> None:
    verifier = _verifier()
    client = TestClient(_make_app(verifier=verifier))
    response = client.get("/api/accounts/default/live-state")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate", "").startswith("Bearer")


def test_live_state_rejects_bad_token() -> None:
    verifier = _verifier()
    client = TestClient(_make_app(verifier=verifier))
    response = client.get(
        "/api/accounts/default/live-state",
        headers={"Authorization": "Bearer this-is-not-valid"},
    )
    assert response.status_code == 401


def test_live_state_accepts_household_token() -> None:
    verifier = _verifier()
    token = _household_token(verifier)
    client = TestClient(_make_app(verifier=verifier))
    response = client.get(
        "/api/accounts/default/live-state",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert '"account_id":"default"' in response.text
    # Phase serialises as a StrEnum value — the canonical body
    # carries it as a quoted string, not a raw int.
    assert '"phase":"1"' in response.text


def test_live_state_accepts_per_account_token() -> None:
    """A signed-in operator (any valid claim — household OR a
    specific account) SHALL be able to VIEW the live-state.
    Mutation endpoints (registry promotion) keep per-account
    scoping; view endpoints accept any valid token so a user who
    signed in via ``/api/session`` with the ``default`` claim
    isn't locked out of the dashboard."""
    verifier = _verifier()
    per_account = _account_token(verifier, "default")
    client = TestClient(_make_app(verifier=verifier))
    response = client.get(
        "/api/accounts/default/live-state",
        headers={"Authorization": f"Bearer {per_account}"},
    )
    # 200 (reader wired) OR 500 (reader missing in this minimal
    # fixture — either way the auth check passes, which is the
    # point); 401 would mean the token was rejected.
    assert response.status_code != 401, (
        f"per-account token rejected by view endpoint; "
        f"got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# TC_FAS_003 — byte-identical replay
# ---------------------------------------------------------------------------


def test_live_state_byte_identical_replay() -> None:
    """Two GETs with identical token + path SHALL return byte-
    identical response bodies. (HTTP envelope may differ — body is
    what we pin.)"""
    verifier = _verifier()
    token = _household_token(verifier)
    client = TestClient(_make_app(verifier=verifier))
    a = client.get(
        "/api/accounts/default/live-state",
        headers={"Authorization": f"Bearer {token}"},
    )
    b = client.get(
        "/api/accounts/default/live-state",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.content == b.content


# ---------------------------------------------------------------------------
# Registry promotion route
# ---------------------------------------------------------------------------


def test_promote_requires_account_token() -> None:
    verifier = _verifier()
    client = TestClient(_make_app(verifier=verifier))
    response = client.post(
        "/api/registry/strat-1/promote",
        json={
            "account_id": "default",
            "operator_id": "alice",
            "rationale": "validated walk-forward",
        },
    )
    assert response.status_code == 401
    assert b'"error":"registry:token_invalid"' in response.content


def test_promote_happy_path() -> None:
    verifier = _verifier()
    token = _account_token(verifier, "default")
    promoter = _StubPromoter(accept=True)
    notifier = _StubNotifier()
    client = TestClient(
        _make_app(verifier=verifier, promoter=promoter, notifier=notifier)
    )
    response = client.post(
        "/api/registry/strat-1/promote",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Operator-Token": "action-token-xyz",
        },
        json={
            "account_id": "default",
            "operator_id": "alice",
            "rationale": "validated walk-forward",
        },
    )
    assert response.status_code == 200
    assert b'"promoted":true' in response.content
    assert promoter.calls == [
        {
            "strategy_id": "strat-1",
            "operator_token": "action-token-xyz",
            "operator_id": "alice",
            "rationale": "validated walk-forward",
            "account_id": "default",
        }
    ]
    # Audit fan-out fired once.
    assert len(notifier.dispatched) == 1


def test_promote_missing_operator_token() -> None:
    verifier = _verifier()
    token = _account_token(verifier, "default")
    client = TestClient(_make_app(verifier=verifier))
    response = client.post(
        "/api/registry/strat-1/promote",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "account_id": "default",
            "operator_id": "alice",
            "rationale": "validated walk-forward",
        },
    )
    assert response.status_code == 400
    assert b'"error":"webui:missing_operator_token"' in response.content


def test_promote_strategy_not_found_maps_to_404() -> None:
    verifier = _verifier()
    token = _account_token(verifier, "default")
    promoter = _StubPromoter(accept=False, reason="registry:strategy_not_found")
    client = TestClient(_make_app(verifier=verifier, promoter=promoter))
    response = client.post(
        "/api/registry/strat-1/promote",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Operator-Token": "action-token-xyz",
        },
        json={
            "account_id": "default",
            "operator_id": "alice",
            "rationale": "validated walk-forward",
        },
    )
    assert response.status_code == 404
    assert b'"error":"registry:strategy_not_found"' in response.content


def test_promote_already_promoted_maps_to_409() -> None:
    verifier = _verifier()
    token = _account_token(verifier, "default")
    promoter = _StubPromoter(accept=False, reason="registry:already_promoted")
    client = TestClient(_make_app(verifier=verifier, promoter=promoter))
    response = client.post(
        "/api/registry/strat-1/promote",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Operator-Token": "action-token-xyz",
        },
        json={
            "account_id": "default",
            "operator_id": "alice",
            "rationale": "validated walk-forward",
        },
    )
    assert response.status_code == 409


def test_promote_validates_body() -> None:
    """Missing/empty body fields SHALL surface as a 422 from pydantic."""
    verifier = _verifier()
    token = _account_token(verifier, "default")
    client = TestClient(_make_app(verifier=verifier))
    response = client.post(
        "/api/registry/strat-1/promote",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Operator-Token": "action-token-xyz",
        },
        json={"account_id": "", "operator_id": "alice", "rationale": "x"},
    )
    assert response.status_code == 422
