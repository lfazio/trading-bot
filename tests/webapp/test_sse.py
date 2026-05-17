"""TC_FAS_005 — SSE live-state stream.

REQ refs:
- REQ_F_FAS_003 — Server-Sent Events at ``GET /events/live-state``;
  media type ``text/event-stream``; event ``id`` is the monotonic
  ``as_of`` ISO-8601 timestamp.
- REQ_NF_FAS_001 — event payload is the canonical-JSON form of the
  same shape ``GET /api/live-state`` returns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId
from trading_system.models.phase import Phase
from trading_system.models.safety import KillSwitchState
from trading_system.webapp import WebappState, create_app
from trading_system.webui.schemas import LiveStateResponse


_NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


class _SingleShotStreamReader:
    """Yields exactly one snapshot then completes — keeps the SSE
    test deterministic + bounded so the TestClient doesn't hang."""

    def live_state(self, *, account_id: AccountId, as_of: datetime) -> LiveStateResponse:
        return _snapshot(account_id, _NOW)

    async def subscribe(
        self, *, account_id: AccountId
    ) -> AsyncIterator[LiveStateResponse]:
        yield _snapshot(account_id, _NOW)


def _snapshot(account_id: AccountId, as_of: datetime) -> LiveStateResponse:
    return LiveStateResponse(
        account_id=account_id,
        as_of=as_of,
        ks_state=KillSwitchState.ACTIVE,
        phase=Phase(1),
        open_positions_count=2,
        equity_after_tax=Decimal("10000.00"),
    )


def _client_with_token() -> tuple[TestClient, str]:
    verifier = AccountScopedTokenVerifier(secret=b"phase-b-secret", ttl_seconds=3600)
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))
    state = WebappState(
        token_verifier=verifier,
        live_state_reader=_SingleShotStreamReader(),
    )
    return TestClient(create_app(state)), token


def test_sse_route_requires_household_token() -> None:
    client, _ = _client_with_token()
    response = client.get("/events/live-state")
    assert response.status_code == 401


def test_sse_route_returns_event_stream_media_type() -> None:
    client, token = _client_with_token()
    with client.stream(
        "GET",
        "/events/live-state",
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")


def test_sse_event_carries_canonical_payload_and_monotonic_id() -> None:
    """The first event SHALL be ``event: live-state`` with
    ``id: <iso8601>`` and a ``data:`` payload equal to the canonical
    serialisation of the equivalent ``/api/live-state`` response."""
    client, token = _client_with_token()
    with client.stream(
        "GET",
        "/events/live-state",
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        # Read just enough to see the first event then close the stream.
        chunks: list[str] = []
        for chunk in response.iter_text():
            chunks.append(chunk)
            if "\n\n" in "".join(chunks):
                break
        text = "".join(chunks)

    assert "event: live-state" in text
    # Event id is the iso8601 timestamp we pinned in the fixture.
    assert f"id: {_NOW.isoformat()}" in text
    # Canonical-JSON payload: keys sorted, Decimal as string.
    assert '"account_id":"default"' in text
    assert '"equity_after_tax":"10000.00"' in text
    assert '"phase":"1"' in text


def test_sse_route_query_param_account_id_override() -> None:
    """Operators may target a non-default account via
    ``?account_id=<other>``."""
    client, token = _client_with_token()
    with client.stream(
        "GET",
        "/events/live-state?account_id=other",
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        chunks: list[str] = []
        for chunk in response.iter_text():
            chunks.append(chunk)
            if "\n\n" in "".join(chunks):
                break
        assert '"account_id":"other"' in "".join(chunks)
