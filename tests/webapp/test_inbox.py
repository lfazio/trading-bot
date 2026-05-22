"""Tests for the notifications inbox (REQ_F_WEB2_009 + REQ_SDD_WEB2_010)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.inbox import (
    INBOX_MAXLEN,
    InboxChannel,
    InboxEntry,
)
from trading_system.webapp.runtimes.paper_trading import RuntimeRegistry


_SECRET = b"inbox-test-secret"


# ---------------------------------------------------------------------------
# InboxChannel unit tests
# ---------------------------------------------------------------------------


def _entry(*, code: str = "x", severity: str = "info") -> InboxEntry:
    return InboxEntry(
        at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        category="paper-session",
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message="...",
        account_id="paper-x",
    )


def test_inbox_appends_in_order() -> None:
    ch = InboxChannel()
    ch.append(_entry(code="a"))
    ch.append(_entry(code="b"))
    snap = ch.snapshot()
    assert [e.code for e in snap] == ["a", "b"]


def test_inbox_evicts_oldest_when_full() -> None:
    """REQ_SDD_WEB2_010 — ring buffer SHALL evict oldest at maxlen."""
    ch = InboxChannel(maxlen=3)
    for i in range(5):
        ch.append(_entry(code=str(i)))
    assert [e.code for e in ch.snapshot()] == ["2", "3", "4"]


def test_inbox_default_maxlen_is_100() -> None:
    assert INBOX_MAXLEN == 100
    ch = InboxChannel()
    assert ch.maxlen == 100


def test_inbox_rejects_bad_severity() -> None:
    with pytest.raises(ValueError, match="severity"):
        InboxEntry(
            at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
            category="x",
            code="y",
            severity="catastrophe",  # type: ignore[arg-type]
            message="...",
        )


def test_inbox_rejects_empty_category_or_code() -> None:
    with pytest.raises(ValueError, match="category"):
        InboxEntry(
            at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
            category="",
            code="x",
            severity="info",
            message="...",
        )
    with pytest.raises(ValueError, match="code"):
        InboxEntry(
            at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
            category="x",
            code="",
            severity="info",
            message="...",
        )


def test_inbox_clear() -> None:
    ch = InboxChannel()
    ch.append(_entry(code="a"))
    ch.clear()
    assert ch.snapshot() == ()


def test_inbox_rejects_bad_maxlen() -> None:
    with pytest.raises(ValueError, match="maxlen"):
        InboxChannel(maxlen=0)


# ---------------------------------------------------------------------------
# Route integration
# ---------------------------------------------------------------------------


def _make_client():
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    inbox = InboxChannel()
    state = WebappState(
        token_verifier=verifier,
        notification_inbox=inbox,
        runtime_registry=RuntimeRegistry(),
    )
    return TestClient(create_app(state)), verifier, inbox


def _household_token(verifier):
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC))


def test_api_inbox_requires_auth() -> None:
    client, _, _ = _make_client()
    response = client.get("/api/inbox")
    assert response.status_code == 401


def test_api_inbox_returns_empty_payload_when_no_entries() -> None:
    client, verifier, _ = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/api/inbox", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == {"entries": []}


def test_api_inbox_returns_appended_entries() -> None:
    client, verifier, inbox = _make_client()
    inbox.append(_entry(code="alpha"))
    inbox.append(_entry(code="beta"))
    token = _household_token(verifier)
    response = client.get(
        "/api/inbox", headers={"Authorization": f"Bearer {token}"}
    )
    body = json.loads(response.content)
    assert len(body["entries"]) == 2
    assert [e["code"] for e in body["entries"]] == ["alpha", "beta"]


def test_view_notifications_redirects_unauth_to_login() -> None:
    client, _, _ = _make_client()
    response = client.get("/notifications", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_view_notifications_renders_table_with_entries() -> None:
    client, verifier, inbox = _make_client()
    inbox.append(_entry(code="alpha"))
    inbox.append(_entry(code="beta"))
    token = _household_token(verifier)
    response = client.get(
        "/notifications", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.text
    # Newest first.
    idx_alpha = body.find("alpha")
    idx_beta = body.find("beta")
    assert 0 <= idx_beta < idx_alpha


def test_view_notifications_renders_empty_state_when_no_entries() -> None:
    client, verifier, _ = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/notifications", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert "No notifications yet" in response.text


# ---------------------------------------------------------------------------
# Producer integration — wizard finish + paper-session stop populate inbox
# ---------------------------------------------------------------------------


def test_onboarding_finish_appends_session_started_entry() -> None:
    client, _, inbox = _make_client()
    client.post(
        "/onboarding/step2",
        data={"starting_capital": "10000", "universe": "eu-dividend-starter"},
    )
    client.post("/onboarding/step3", data={"strategy": "CoreStrategy"})
    client.post("/onboarding/finish", follow_redirects=False)
    snap = inbox.snapshot()
    assert len(snap) == 1
    assert snap[0].code == "session_started"
    assert snap[0].category == "paper-session"
    assert "CoreStrategy" in snap[0].message
    assert snap[0].account_id.startswith("paper-")


def test_paper_session_stop_appends_session_stopped_entry() -> None:
    client, verifier, inbox = _make_client()
    # Walk the wizard.
    client.post(
        "/onboarding/step2",
        data={"starting_capital": "10000", "universe": "eu-dividend-starter"},
    )
    client.post("/onboarding/step3", data={"strategy": "CoreStrategy"})
    response = client.post("/onboarding/finish", follow_redirects=False)
    from urllib.parse import unquote

    aid = unquote(response.headers["location"].split("account_id=", 1)[1])
    # Stop it.
    token = _household_token(verifier)
    client.post(
        f"/paper-sessions/{aid}/stop",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    codes = [e.code for e in inbox.snapshot()]
    assert "session_started" in codes
    assert "session_stopped" in codes


def test_paper_session_stop_skips_inbox_entry_when_session_not_live() -> None:
    """Stopping a non-existent session SHALL NOT spam the inbox
    on every refresh."""
    client, verifier, inbox = _make_client()
    token = _household_token(verifier)
    client.post(
        "/paper-sessions/paper-not-real/stop",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert inbox.snapshot() == ()
