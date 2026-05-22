"""Tests for the paper-trading state surface (REQ_F_WEB2_003).

Covers both halves of the dashboard panel wiring:
- `GET /api/accounts/{account_id}/paper-state` request-response,
- `GET /events/paper-state` SSE channel.

REQ refs:
- REQ_F_WEB2_003 — operator-facing read shape per paper session.
- REQ_NF_WEB2_001 — canonical JSON; byte-identical replay.
- REQ_SDD_FAS_001 — router consumes a Protocol-shaped reader.
- REQ_F_PAP_002 — degraded banner data reaches the panel via the
  ``is_degraded`` field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import AccountId
from trading_system.models.money import Currency, Money
from trading_system.result import Nothing, Some
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.paper_state_reader import RuntimePaperStateReader
from trading_system.webui.schemas import PaperStateResponse


_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
_PAPER_AID = AccountId("paper-2026-05-22T12:00:00+00:00")


# ---------------------------------------------------------------------------
# Fixtures — fake runtime + fake registry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakeRuntime:
    alive: bool = True
    degraded: bool = False
    degraded_at: datetime | None = None
    last_tick: datetime | None = None
    points: list[EquityPoint] = field(default_factory=list)

    def is_alive(self) -> bool:
        return self.alive

    def is_degraded(self) -> bool:
        return self.degraded

    def degraded_since(self) -> datetime | None:
        return self.degraded_at

    def last_tick_at(self) -> datetime | None:
        return self.last_tick

    def equity_history(self) -> tuple[EquityPoint, ...]:
        return tuple(self.points)


@dataclass(slots=True)
class _FakeRegistry:
    """Holds at most one fake runtime per account_id; returns the
    same ``Option`` shape the real ``RuntimeRegistry`` does."""

    runtimes: dict[AccountId, _FakeRuntime] = field(default_factory=dict)

    def status(self, account_id: AccountId):
        runtime = self.runtimes.get(account_id)
        if runtime is None:
            return Nothing()
        return Some(runtime)


def _point(*, at: datetime, after_tax: str) -> EquityPoint:
    return EquityPoint(
        at=at,
        equity_gross=Money(Decimal(after_tax), Currency.EUR),
        equity_after_tax=Money(Decimal(after_tax), Currency.EUR),
        drawdown_pct=Decimal("0"),
    )


def _verifier() -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(secret=b"paper-panel", ttl_seconds=3600)


def _household_token(verifier: AccountScopedTokenVerifier) -> str:
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))


def _make_app(
    *,
    verifier: AccountScopedTokenVerifier,
    reader: RuntimePaperStateReader,
):
    state = WebappState(
        token_verifier=verifier,
        paper_state_reader=reader,
    )
    return create_app(state)


# ---------------------------------------------------------------------------
# Reader unit tests
# ---------------------------------------------------------------------------


def test_paper_state_returns_no_session_when_not_registered() -> None:
    """When no runtime is registered, the reader SHALL return the
    documented all-zeroed shape with ``is_alive=False``."""
    reader = RuntimePaperStateReader(registry=_FakeRegistry())
    snap = reader.paper_state(account_id=_PAPER_AID, as_of=_NOW)
    assert isinstance(snap, PaperStateResponse)
    assert snap.account_id == _PAPER_AID
    assert snap.is_alive is False
    assert snap.is_degraded is False
    assert snap.equity_points_count == 0
    assert snap.latest_equity_after_tax is None


def test_paper_state_reflects_live_runtime_state() -> None:
    """REQ_F_WEB2_003 — the reader surfaces the runtime's alive /
    degraded / last_tick / equity counts faithfully."""
    runtime = _FakeRuntime(
        alive=True,
        degraded=True,
        degraded_at=_NOW - timedelta(seconds=30),
        last_tick=_NOW,
        points=[
            _point(at=_NOW - timedelta(minutes=1), after_tax="1000"),
            _point(at=_NOW, after_tax="1007"),
        ],
    )
    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_PAPER_AID: runtime})
    )
    snap = reader.paper_state(account_id=_PAPER_AID, as_of=_NOW)
    assert snap.is_alive is True
    assert snap.is_degraded is True
    assert snap.degraded_since == _NOW - timedelta(seconds=30)
    assert snap.last_tick_at == _NOW
    assert snap.equity_points_count == 2
    assert snap.latest_equity_after_tax == Decimal("1007")


def test_paper_state_reader_rejects_non_positive_tick_seconds() -> None:
    with pytest.raises(ValueError, match="tick_seconds must be > 0"):
        RuntimePaperStateReader(registry=_FakeRegistry(), tick_seconds=0)
    with pytest.raises(ValueError, match="tick_seconds must be > 0"):
        RuntimePaperStateReader(registry=_FakeRegistry(), tick_seconds=-1.5)


# ---------------------------------------------------------------------------
# Route — request/response
# ---------------------------------------------------------------------------


def test_paper_state_route_requires_bearer_token() -> None:
    reader = RuntimePaperStateReader(registry=_FakeRegistry())
    client = TestClient(_make_app(verifier=_verifier(), reader=reader))
    response = client.get(f"/api/accounts/{_PAPER_AID}/paper-state")
    assert response.status_code == 401


def test_paper_state_route_returns_no_session_payload() -> None:
    """REQ_F_WEB2_003 — no live session ⇒ all-zeroed shape."""
    verifier = _verifier()
    reader = RuntimePaperStateReader(registry=_FakeRegistry())
    client = TestClient(_make_app(verifier=verifier, reader=reader))
    token = _household_token(verifier)
    response = client.get(
        f"/api/accounts/{_PAPER_AID}/paper-state",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["account_id"] == str(_PAPER_AID)
    assert body["is_alive"] is False
    assert body["is_degraded"] is False
    assert body["equity_points_count"] == 0
    assert body["latest_equity_after_tax"] is None


def test_paper_state_route_returns_live_session_payload() -> None:
    verifier = _verifier()
    runtime = _FakeRuntime(
        alive=True,
        degraded=False,
        last_tick=_NOW,
        points=[_point(at=_NOW, after_tax="1234.56")],
    )
    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_PAPER_AID: runtime})
    )
    client = TestClient(_make_app(verifier=verifier, reader=reader))
    token = _household_token(verifier)
    response = client.get(
        f"/api/accounts/{_PAPER_AID}/paper-state",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["is_alive"] is True
    assert body["equity_points_count"] == 1
    # Decimal serialises as a string per the canonical-JSON contract.
    assert body["latest_equity_after_tax"] == "1234.56"


def test_paper_state_route_byte_identical_on_pinned_clock() -> None:
    """REQ_NF_WEB2_001 — when the wired reader pins ``as_of``, the
    request body is byte-identical across replays."""
    verifier = _verifier()
    # Pinned reader: a tiny adapter that fixes the as_of so the
    # body shape is fully deterministic.
    base = RuntimePaperStateReader(registry=_FakeRegistry())

    class _Pinned:
        def paper_state(self, *, account_id, as_of):
            del as_of
            return base.paper_state(account_id=account_id, as_of=_NOW)

    client = TestClient(_make_app(verifier=verifier, reader=_Pinned()))  # type: ignore[arg-type]
    token = _household_token(verifier)
    a = client.get(
        f"/api/accounts/{_PAPER_AID}/paper-state",
        headers={"Authorization": f"Bearer {token}"},
    ).content
    b = client.get(
        f"/api/accounts/{_PAPER_AID}/paper-state",
        headers={"Authorization": f"Bearer {token}"},
    ).content
    assert a == b


# ---------------------------------------------------------------------------
# SSE channel — auth + subscribe iterator
# ---------------------------------------------------------------------------


def test_paper_state_sse_requires_auth() -> None:
    """The SSE channel SHALL require the same Bearer-token gate as
    the request-response route."""
    reader = RuntimePaperStateReader(registry=_FakeRegistry())
    client = TestClient(_make_app(verifier=_verifier(), reader=reader))
    response = client.get(
        f"/events/paper-state?account_id={_PAPER_AID}",
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_paper_state_subscribe_yields_snapshots() -> None:
    """REQ_F_WEB2_003 — ``subscribe`` SHALL yield one
    ``PaperStateResponse`` per ``tick_seconds``. The TestClient
    SSE round-trip is awkward (the stream never closes from the
    server side), so we exercise the async iterator directly —
    this is what the SSE router consumes.
    """
    runtime = _FakeRuntime(
        alive=True,
        last_tick=_NOW,
        points=[_point(at=_NOW, after_tax="1000")],
    )
    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_PAPER_AID: runtime}),
        tick_seconds=0.01,
    )
    stream = reader.subscribe(account_id=_PAPER_AID)
    seen: list[PaperStateResponse] = []
    async for snap in stream:
        seen.append(snap)
        if len(seen) >= 3:
            break
    assert len(seen) == 3
    for snap in seen:
        assert snap.account_id == _PAPER_AID
        assert snap.is_alive is True
        assert snap.equity_points_count == 1
        assert snap.latest_equity_after_tax == Decimal("1000")
