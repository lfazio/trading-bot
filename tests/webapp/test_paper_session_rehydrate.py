"""CR-019 §6 — paper-session auto-rehydration POST handler tests.

REQ refs:
- REQ_F_PAP_003 — webapp restart resumes the session cleanly
  without operator action (now actually true after this slice).
- REQ_SDD_WEB2_005 — `resume_from_persistence` enrichment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.money import Currency, Money
from trading_system.persistence.repositories.paper_sessions import (
    PaperSessionRow,
)
from trading_system.result import Err, Ok
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.runtimes.paper_trading import RuntimeRegistry


_SECRET = b"rehydrate-secret-fixture-padding-of-the-required-length"
_T0 = datetime(2026, 5, 31, 12, tzinfo=UTC)
_ACCOUNT = AccountId("paper-2026-05-31T11:00:00+00:00")


@dataclass
class _FakePaperSessionRepo:
    """In-memory PaperSessionRepository — Protocol satisfier."""

    rows: dict = field(default_factory=dict)

    def get(self, account_id: AccountId):
        row = self.rows.get(str(account_id))
        return Ok(row)

    def append_session(self, row: PaperSessionRow):
        if str(row.account_id) in self.rows:
            return Err(
                f"persistence:integrity:paper_sessions:duplicate:{row.account_id}"
            )
        self.rows[str(row.account_id)] = row
        return Ok(None)

    def list_all(self):
        return Ok(tuple(self.rows.values()))


def _row(*, universe: str = "eu-dividend-starter") -> PaperSessionRow:
    return PaperSessionRow(
        account_id=_ACCOUNT,
        universe=universe,
        strategy_id=StrategyId("CoreStrategy"),
        instrument_symbol="ASML",
        starting_capital=Money(Decimal("10000"), Currency.EUR),
        bar_source="simulated",
        started_at=_T0,
    )


def _client_with_metadata(*, row: PaperSessionRow | None = None):
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    registry = RuntimeRegistry()
    repo = _FakePaperSessionRepo()
    if row is not None:
        repo.rows[str(row.account_id)] = row
    state = WebappState(
        token_verifier=verifier,
        runtime_registry=registry,
        paper_session_repository=repo,
    )
    client = TestClient(create_app(state))
    return client, verifier, registry, repo


def _bearer(verifier: AccountScopedTokenVerifier) -> dict:
    token = verifier.issue(
        account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC)
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_rehydrate_route_requires_auth() -> None:
    """POST without a valid bearer ⇒ 401."""
    client, _v, _r, _repo = _client_with_metadata(row=_row())
    response = client.post(
        f"/paper-sessions/{_ACCOUNT}/rehydrate", follow_redirects=False
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_rehydrate_happy_path_registers_runtime() -> None:
    """REQ_F_PAP_003 — auth-gated POST rehydrates the persisted
    session + 303-redirects to the dashboard with the resumed
    account cookie + flash."""
    row = _row()
    client, verifier, registry, _repo = _client_with_metadata(row=row)
    response = client.post(
        f"/paper-sessions/{_ACCOUNT}/rehydrate",
        headers=_bearer(verifier),
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/?account_id=")
    # Active-paper-session cookie was set so the dashboard reads
    # the resumed runtime.
    set_cookie = response.headers.get("set-cookie", "")
    assert "active-paper-session" in set_cookie
    # Flash cookie carries the success category.
    assert "paper-rehydrate-flash" in set_cookie
    # Runtime registered.
    assert _ACCOUNT in registry.live_account_ids()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_rehydrate_already_running_returns_flash_no_double_register() -> None:
    """Second rehydrate while the session is already live ⇒
    303 + categorised flash; registry unchanged."""
    row = _row()
    client, verifier, registry, _repo = _client_with_metadata(row=row)
    first = client.post(
        f"/paper-sessions/{_ACCOUNT}/rehydrate",
        headers=_bearer(verifier),
        follow_redirects=False,
    )
    assert first.status_code == 303
    second = client.post(
        f"/paper-sessions/{_ACCOUNT}/rehydrate",
        headers=_bearer(verifier),
        follow_redirects=False,
    )
    assert second.status_code == 303
    flash = second.headers.get("set-cookie", "")
    assert "paper:rehydrate:already_running" in flash
    # Still one live session — no double-register.
    assert list(registry.live_account_ids()).count(_ACCOUNT) == 1


# ---------------------------------------------------------------------------
# Missing metadata
# ---------------------------------------------------------------------------


def test_rehydrate_missing_metadata_surfaces_categorised_flash() -> None:
    """When no PaperSessionRow exists for the requested
    account_id (e.g., pre-§6 session that never wrote one), the
    handler surfaces `paper:rehydrate:session_not_found:<id>` in
    the flash. No runtime registered."""
    client, verifier, registry, _repo = _client_with_metadata(row=None)
    response = client.post(
        f"/paper-sessions/{_ACCOUNT}/rehydrate",
        headers=_bearer(verifier),
        follow_redirects=False,
    )
    assert response.status_code == 303
    flash = response.headers.get("set-cookie", "")
    assert "paper:rehydrate:session_not_found" in flash
    assert _ACCOUNT not in registry.live_account_ids()


def test_rehydrate_unwired_persistence_surfaces_not_configured_flash() -> None:
    """When `paper_session_repository` slot is unwired ⇒
    `paper:rehydrate:not_configured`."""
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    registry = RuntimeRegistry()
    # No paper_session_repository slot wired.
    state = WebappState(
        token_verifier=verifier,
        runtime_registry=registry,
    )
    client = TestClient(create_app(state))
    response = client.post(
        f"/paper-sessions/{_ACCOUNT}/rehydrate",
        headers=_bearer(verifier),
        follow_redirects=False,
    )
    assert response.status_code == 303
    flash = response.headers.get("set-cookie", "")
    assert "paper:rehydrate:not_configured" in flash
