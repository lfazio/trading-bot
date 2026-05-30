"""CR-029 / TC_PER_BAR_005 + TC_PER_BAR_006 — GET /api/accounts/{aid}/bars.

REQ refs:
- REQ_F_PER_013 — endpoint shape + canonical-JSON byte-determinism.
- REQ_SDD_PER_013 — auth + query-param validation + Err codes.
- REQ_F_ACC_010 — household-claim REJECTED.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.data.types import Bar
from trading_system.models.identifiers import AccountId, InstrumentId
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.instrument_bars import (
    InstrumentBarRepository,
)
from trading_system.result import Err, Ok
from trading_system.webapp import WebappState, create_app


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = (
    _REPO_ROOT / "trading_system" / "persistence" / "migrations"
)
_SECRET = b"bars-route-secret" * 4
_AID = AccountId("paper-alpha-2026")
_IID = InstrumentId("AAA.PA")
_T0 = datetime(2026, 5, 30, 12, tzinfo=UTC)


def _verifier() -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)


def _account_token(v: AccountScopedTokenVerifier, aid: str = str(_AID)) -> str:
    return v.issue(account_id=aid, now=datetime.now(UTC))


def _household_token(v: AccountScopedTokenVerifier) -> str:
    return v.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))


def _bar(close: str, at: datetime) -> Bar:
    p = Decimal(close)
    return Bar(
        at=at,
        open=p,
        high=p * Decimal("1.005"),
        low=p * Decimal("0.995"),
        close=p,
        volume=Decimal("1000"),
    )


class _FakeBarRepo:
    """In-memory `InstrumentBarRepository` Protocol satisfier.

    The SQLite-backed repository can't survive the
    fixture-vs-request thread boundary inside FastAPI's TestClient
    (the connection is owned by the fixture thread). The fake
    swaps it out cleanly + asserts the Protocol contract."""

    def __init__(self):
        self.rows: list[tuple] = []

    def bars_for(self, *, account_id, instrument_id, start, end):
        out = sorted(
            (
                b
                for (aid, iid, b) in self.rows
                if aid == str(account_id)
                and iid == str(instrument_id)
                and start <= b.at <= end
            ),
            key=lambda b: b.at,
        )
        return Ok(tuple(out))

    def append_bars(self, rows, *, account_id):
        for instrument_id, bar in rows:
            self.rows.append((str(account_id), str(instrument_id), bar))
        return Ok(None)


@pytest.fixture
def wired_app():  # type: ignore[no-untyped-def]
    repo = _FakeBarRepo()
    repo.append_bars(
        [
            (_IID, _bar("10.00", _T0)),
            (_IID, _bar("11.00", _T0 + timedelta(days=1))),
            (_IID, _bar("12.00", _T0 + timedelta(days=2))),
            (InstrumentId("BBB.PA"), _bar("20.00", _T0)),
        ],
        account_id=_AID,
    )
    verifier = _verifier()
    state = WebappState(
        token_verifier=verifier,
        instrument_bar_repository=repo,
    )
    app = create_app(state)
    yield app, verifier


# ---------------------------------------------------------------------------
# Happy path + byte-determinism
# ---------------------------------------------------------------------------


def test_get_bars_happy_path_returns_canonical_json_byte_identical(
    wired_app,
) -> None:
    """REQ_F_PER_013 / REQ_SDD_PER_013 — two reads against the same
    repository state SHALL be byte-identical."""
    app, verifier = wired_app
    client = TestClient(app)
    token = _account_token(verifier)
    headers = {"Authorization": f"Bearer {token}"}
    start = _T0.isoformat()
    end = (_T0 + timedelta(days=2)).isoformat()
    r1 = client.get(
        f"/api/accounts/{quote(str(_AID), safe='')}/bars",
        params={"instrument": str(_IID), "start": start, "end": end},
        headers=headers,
    )
    r2 = client.get(
        f"/api/accounts/{quote(str(_AID), safe='')}/bars",
        params={"instrument": str(_IID), "start": start, "end": end},
        headers=headers,
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200
    assert r1.content == r2.content
    body = json.loads(r1.content)
    closes = [b["close"] for b in body["bars"]]
    assert closes == ["10.00", "11.00", "12.00"]


# ---------------------------------------------------------------------------
# Auth: household REJECTED + cross-account REJECTED + missing
# ---------------------------------------------------------------------------


def test_get_bars_household_claim_rejected_403(wired_app) -> None:
    app, verifier = wired_app
    client = TestClient(app)
    token = _household_token(verifier)
    r = client.get(
        f"/api/accounts/{quote(str(_AID), safe='')}/bars",
        params={
            "instrument": str(_IID),
            "start": _T0.isoformat(),
            "end": _T0.isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    body = json.loads(r.content)
    assert body.get("error") == "registry:household_claim_rejected"


def test_get_bars_cross_account_token_rejected_401(wired_app) -> None:
    app, verifier = wired_app
    client = TestClient(app)
    token = _account_token(verifier, aid="other-account")
    r = client.get(
        f"/api/accounts/{quote(str(_AID), safe='')}/bars",
        params={
            "instrument": str(_IID),
            "start": _T0.isoformat(),
            "end": _T0.isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_get_bars_missing_authorization_401(wired_app) -> None:
    app, _verifier_ = wired_app
    client = TestClient(app)
    r = client.get(
        f"/api/accounts/{quote(str(_AID), safe='')}/bars",
        params={
            "instrument": str(_IID),
            "start": _T0.isoformat(),
            "end": _T0.isoformat(),
        },
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Query-param validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_param",
    ["instrument", "start", "end"],
)
def test_get_bars_missing_query_param_400(wired_app, missing_param) -> None:
    """REQ_SDD_PER_013 — missing param ⇒ 400 with categorised Err."""
    app, verifier = wired_app
    client = TestClient(app)
    token = _account_token(verifier)
    params = {
        "instrument": str(_IID),
        "start": _T0.isoformat(),
        "end": _T0.isoformat(),
    }
    params[missing_param] = ""  # blank out one
    r = client.get(
        f"/api/accounts/{quote(str(_AID), safe='')}/bars",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    body = json.loads(r.content)
    assert body.get("error") == f"webapp:missing_query_param:{missing_param}"


def test_get_bars_bad_iso_datetime_400(wired_app) -> None:
    app, verifier = wired_app
    client = TestClient(app)
    token = _account_token(verifier)
    r = client.get(
        f"/api/accounts/{quote(str(_AID), safe='')}/bars",
        params={
            "instrument": str(_IID),
            "start": "not-a-date",
            "end": _T0.isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    body = json.loads(r.content)
    assert "webapp:bad_iso_datetime" in body.get("error", "")


def test_get_bars_repository_missing_500(tmp_path: Path) -> None:
    """REQ_SDD_PER_013 — unwired repository ⇒ 500 with categorised
    Err. Operator-visible signal."""
    verifier = _verifier()
    app = create_app(WebappState(token_verifier=verifier))
    client = TestClient(app)
    token = _account_token(verifier)
    r = client.get(
        f"/api/accounts/{quote(str(_AID), safe='')}/bars",
        params={
            "instrument": str(_IID),
            "start": _T0.isoformat(),
            "end": _T0.isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 500
    body = json.loads(r.content)
    assert body.get("error") == "webapp:instrument_bar_repository_missing"
