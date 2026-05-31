"""CR-024 §7 — operator-token lifecycle endpoint tests.

REQ refs:
- REQ_F_TOK_003 — rotate_secret atomic flip; previous_secret
  grace window keeps existing tokens verifying.
- REQ_F_TOK_002 — revocation via OperatorTokenRevocationRepository
  precedes TTL check.
- REQ_NF_TOK_001 — SECURITY audit on every lifecycle event;
  raw token never in payload.
- REQ_F_ACC_010 — household scope for rotate; per-account for
  revoke.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok
from trading_system.webapp import WebappState, create_app


_SECRET = b"operator-tokens-secret-padding-of-the-required-length"
_AID = "paper-alpha-2026"


def _verifier() -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)


def _household_token(v: AccountScopedTokenVerifier) -> str:
    return v.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))


def _account_token(v: AccountScopedTokenVerifier, aid: str = _AID) -> str:
    return v.issue(account_id=aid, now=datetime.now(UTC))


@dataclass
class _FakeRevocationRepo:
    """In-memory OperatorTokenRevocationRepository — Protocol
    satisfier."""

    rows: list = field(default_factory=list)

    def revoke(self, *, account_id: AccountId, jti: str, reason: str = ""):
        if not jti.strip():
            return Err("persistence:bad_input:revoke:empty_jti")
        # Idempotent on duplicate.
        for r in self.rows:
            if r["account_id"] == str(account_id) and r["jti"] == jti:
                return Ok(None)
        self.rows.append(
            {
                "account_id": str(account_id),
                "jti": jti,
                "reason": reason,
                "revoked_at": datetime.now(tz=UTC),
            }
        )
        return Ok(None)

    def is_revoked(self, *, account_id: AccountId, jti: str):
        for r in self.rows:
            if r["account_id"] == str(account_id) and r["jti"] == jti:
                return Ok(True)
        return Ok(False)

    def list_all(self, *, account_id: AccountId | None = None):
        rows = (
            [r for r in self.rows if r["account_id"] == str(account_id)]
            if account_id is not None
            else self.rows
        )
        # Convert dicts to mock TokenRevocation rows the route's
        # serialiser can read via getattr.
        @dataclass
        class _Row:
            jti: str
            reason: str
            revoked_at: datetime

        return Ok(
            tuple(_Row(jti=r["jti"], reason=r["reason"], revoked_at=r["revoked_at"]) for r in rows)
        )


def _make_app(*, repo: _FakeRevocationRepo | None = None):
    state = WebappState(
        token_verifier=_verifier(),
        operator_token_revocation_repo=repo or _FakeRevocationRepo(),
    )
    return create_app(state)


# ---------------------------------------------------------------------------
# Rotation endpoint
# ---------------------------------------------------------------------------


def test_rotate_secret_happy_path_returns_new_secret() -> None:
    """Household-token-gated rotation returns a fresh 64-byte
    hex secret + the previous secret enters the grace window
    so the rotating operator's token still verifies."""
    app = _make_app()
    client = TestClient(app)
    verifier = app.state.token_verifier
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC)
    )
    response = client.post(
        "/api/operator/rotate-secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = json.loads(response.content)
    assert "new_secret_hex" in body
    # 64 bytes ⇒ 128 hex chars.
    assert len(body["new_secret_hex"]) == 128
    assert "rotated_at" in body
    # Existing household token signed with the OLD secret SHALL
    # still verify because the verifier's previous_secret slot
    # holds it for one grace window.
    assert verifier.verify(token, account_id=HOUSEHOLD_CLAIM)


def test_rotate_secret_rejects_missing_authorization() -> None:
    app = _make_app()
    client = TestClient(app)
    response = client.post("/api/operator/rotate-secret")
    assert response.status_code == 401


def test_rotate_secret_rejects_per_account_token() -> None:
    """Rotation is HOUSEHOLD-only — per-account tokens get
    `registry:household_required` (REQ_F_ACC_010 discipline)."""
    app = _make_app()
    client = TestClient(app)
    verifier = app.state.token_verifier
    aid_token = verifier.issue(account_id=_AID, now=datetime.now(UTC))
    response = client.post(
        "/api/operator/rotate-secret",
        headers={"Authorization": f"Bearer {aid_token}"},
    )
    assert response.status_code == 403
    body = json.loads(response.content)
    assert body.get("error") == "registry:household_required"


# ---------------------------------------------------------------------------
# Revoke endpoint
# ---------------------------------------------------------------------------


def test_revoke_token_happy_path_writes_to_repo() -> None:
    """Per-account-token-gated revoke adds the (account_id, jti)
    row to the repo + returns 200 with the canonical-JSON body."""
    repo = _FakeRevocationRepo()
    app = _make_app(repo=repo)
    client = TestClient(app)
    verifier = app.state.token_verifier
    token = verifier.issue(account_id=_AID, now=datetime.now(UTC))
    jti = "abc123def4567890abc123def4567890"
    response = client.post(
        f"/api/operator/accounts/{quote(_AID, safe='')}/tokens/{jti}/revoke",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = json.loads(response.content)
    assert body == {
        "account_id": _AID,
        "jti": jti,
        "revoked": True,
    }
    # Row landed in the repo.
    assert len(repo.rows) == 1
    assert repo.rows[0]["jti"] == jti


def test_revoke_token_idempotent_on_duplicate() -> None:
    """REQ_F_TOK_002 — re-revoking is idempotent; the repository
    returns Ok(None) without raising."""
    repo = _FakeRevocationRepo()
    app = _make_app(repo=repo)
    client = TestClient(app)
    verifier = app.state.token_verifier
    token = verifier.issue(account_id=_AID, now=datetime.now(UTC))
    jti = "abc123def4567890abc123def4567890"
    url = f"/api/operator/accounts/{quote(_AID, safe='')}/tokens/{jti}/revoke"
    first = client.post(
        url, headers={"Authorization": f"Bearer {token}"}
    )
    second = client.post(
        url, headers={"Authorization": f"Bearer {token}"}
    )
    assert first.status_code == 200
    assert second.status_code == 200
    # Only one row written.
    assert len(repo.rows) == 1


def test_revoke_token_household_claim_rejected() -> None:
    """Household claim REJECTED on the revoke endpoint
    (per-account scoping)."""
    app = _make_app()
    client = TestClient(app)
    verifier = app.state.token_verifier
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC)
    )
    jti = "abc123def4567890abc123def4567890"
    response = client.post(
        f"/api/operator/accounts/{quote(_AID, safe='')}/tokens/{jti}/revoke",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    body = json.loads(response.content)
    assert body.get("error") == "registry:household_claim_rejected"


def test_revoke_token_cross_account_rejected() -> None:
    """A token for account_id=A SHALL NOT revoke a token whose
    revocation row is scoped to account_id=B."""
    app = _make_app()
    client = TestClient(app)
    verifier = app.state.token_verifier
    token_a = verifier.issue(account_id="account-a", now=datetime.now(UTC))
    response = client.post(
        f"/api/operator/accounts/account-b/tokens/some-jti/revoke",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response.status_code == 401


def test_revoke_token_missing_repo_returns_500() -> None:
    state = WebappState(token_verifier=_verifier())
    app = create_app(state)
    client = TestClient(app)
    verifier = app.state.token_verifier
    token = verifier.issue(account_id=_AID, now=datetime.now(UTC))
    response = client.post(
        f"/api/operator/accounts/{quote(_AID, safe='')}/tokens/abc/revoke",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 500
    body = json.loads(response.content)
    assert "operator_token_revocation_repo_missing" in body.get("error", "")


# ---------------------------------------------------------------------------
# View route: /operator/tokens
# ---------------------------------------------------------------------------


def test_operator_tokens_view_renders_rotate_and_revoke_forms() -> None:
    """REQ_F_TOK_002 / REQ_F_TOK_003 — view ships both forms +
    the revoked-tokens table; auth-gated."""
    repo = _FakeRevocationRepo()
    app = _make_app(repo=repo)
    client = TestClient(app)
    verifier = app.state.token_verifier
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC)
    )
    response = client.get(
        "/operator/tokens",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.text
    # Rotate section + revoke section + revoked-tokens table.
    assert 'id="rotate-secret"' in body
    assert 'id="revoke-token"' in body
    assert 'id="revoked-tokens"' in body
    # POST endpoints are bound via hx-post.
    assert 'hx-post="/api/operator/rotate-secret"' in body


def test_operator_tokens_view_redirects_when_unauthenticated() -> None:
    """REQ_F_TOK_002 — view requires a valid token claim."""
    app = _make_app()
    client = TestClient(app)
    response = client.get("/operator/tokens", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers.get("location") == "/login"


def test_operator_tokens_view_shows_seeded_revocations() -> None:
    """REQ_F_TOK_002 — the rendered HTML includes every persisted
    revocation jti for the operator's account_id."""
    repo = _FakeRevocationRepo()
    repo.revoke(account_id=AccountId("default"), jti="abc123def", reason="leak")
    repo.revoke(
        account_id=AccountId("other-account"), jti="xyz", reason="op"
    )
    app = _make_app(repo=repo)
    client = TestClient(app)
    verifier = app.state.token_verifier
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC)
    )
    response = client.get(
        "/operator/tokens?account_id=default",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.text
    # The "default" account's jti renders; the other-account
    # jti does NOT (per-account scoping).
    assert "abc123def" in body
    assert "xyz" not in body


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_list_revoked_tokens_returns_per_account_rows() -> None:
    """REQ_F_TOK_002 / REQ_F_ACC_010 — list scopes to the
    requested account_id (cross-account rows excluded)."""
    repo = _FakeRevocationRepo()
    app = _make_app(repo=repo)
    client = TestClient(app)
    verifier = app.state.token_verifier
    # Seed two revocations across accounts.
    repo.revoke(account_id=AccountId(_AID), jti="jti-1", reason="op")
    repo.revoke(account_id=AccountId("other"), jti="jti-2", reason="op")
    token = verifier.issue(account_id=_AID, now=datetime.now(UTC))
    response = client.get(
        f"/api/operator/accounts/{quote(_AID, safe='')}/tokens/revoked",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["account_id"] == _AID
    jtis = [r["jti"] for r in body["revoked"]]
    assert "jti-1" in jtis
    assert "jti-2" not in jtis
