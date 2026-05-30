"""CR-019 step 2 / TC_LIV_008 + TC_LIV_009 — Live-mode FastAPI routes.

Four routes under `/api/accounts/{account_id}/`:
  POST /live-mode/enable
  POST /live-mode/disable
  POST /emergency-stop
  POST /broker-reconnect

All four are per-account-token gated; the household claim is
REJECTED on all four (REQ_F_LIV_008). Every authorised action
emits a SECURITY structured-log entry (REQ_NF_TOK_001).

REQ refs: REQ_F_LIV_002, REQ_F_LIV_003, REQ_F_LIV_006, REQ_F_LIV_008,
REQ_SDD_LIV_005, REQ_NF_TOK_001.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId
from trading_system.observability import configure_logging
from trading_system.result import Err, Ok, Result
from trading_system.webapp import WebappState, create_app


_SECRET = b"live-mode-route-secret" * 4


# ---------------------------------------------------------------------------
# Fake controllers
# ---------------------------------------------------------------------------


@dataclass
class _FakeLiveModeController:
    enable_response: Result[None, str] = field(default_factory=lambda: Ok(None))
    disable_response: Result[None, str] = field(default_factory=lambda: Ok(None))
    enable_calls: list[AccountId] = field(default_factory=list)
    disable_calls: list[AccountId] = field(default_factory=list)

    def enable(self, account_id: AccountId) -> Result[None, str]:
        self.enable_calls.append(account_id)
        return self.enable_response

    def disable(self, account_id: AccountId) -> Result[None, str]:
        self.disable_calls.append(account_id)
        return self.disable_response


@dataclass
class _FakeEmergencyStopController:
    trigger_response: Result[None, str] = field(default_factory=lambda: Ok(None))
    trigger_calls: list[AccountId] = field(default_factory=list)

    def trigger(self, account_id: AccountId) -> Result[None, str]:
        self.trigger_calls.append(account_id)
        return self.trigger_response


@dataclass
class _FakeBrokerReconnectController:
    reconnect_response: Result[None, str] = field(default_factory=lambda: Ok(None))
    reconnect_calls: list[AccountId] = field(default_factory=list)

    def reconnect(self, account_id: AccountId) -> Result[None, str]:
        self.reconnect_calls.append(account_id)
        return self.reconnect_response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _verifier() -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)


def _household_token(verifier: AccountScopedTokenVerifier) -> str:
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))


def _account_token(
    verifier: AccountScopedTokenVerifier, account_id: str
) -> str:
    return verifier.issue(account_id=account_id, now=datetime.now(UTC))


def _make_app(
    *,
    verifier: AccountScopedTokenVerifier,
    live_mode: _FakeLiveModeController | None = None,
    emergency: _FakeEmergencyStopController | None = None,
    reconnect: _FakeBrokerReconnectController | None = None,
):
    state = WebappState(
        token_verifier=verifier,
        live_mode_controller=live_mode or _FakeLiveModeController(),
        emergency_stop_controller=emergency or _FakeEmergencyStopController(),
        broker_reconnect_controller=reconnect or _FakeBrokerReconnectController(),
    )
    return create_app(state)


# ---------------------------------------------------------------------------
# TC_LIV_008 — auth: household REJECTED on all four routes
# ---------------------------------------------------------------------------


ROUTE_PATHS = [
    "/api/accounts/live-alpha-2026/live-mode/enable",
    "/api/accounts/live-alpha-2026/live-mode/disable",
    "/api/accounts/live-alpha-2026/emergency-stop",
    "/api/accounts/live-alpha-2026/broker-reconnect",
]


@pytest.mark.parametrize("path", ROUTE_PATHS)
def test_no_bearer_token_returns_401(path: str) -> None:
    verifier = _verifier()
    client = TestClient(_make_app(verifier=verifier))
    response = client.post(path)
    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "registry:token_invalid"


@pytest.mark.parametrize("path", ROUTE_PATHS)
def test_household_claim_rejected_on_all_four_routes(path: str) -> None:
    """REQ_F_LIV_008 — household claim SHALL NEVER authorise a
    live-mode write."""
    verifier = _verifier()
    client = TestClient(_make_app(verifier=verifier))
    response = client.post(
        path,
        headers={"Authorization": f"Bearer {_household_token(verifier)}"},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "live:household_claim_rejected"


@pytest.mark.parametrize("path", ROUTE_PATHS)
def test_mismatched_account_claim_rejected(path: str) -> None:
    """A token whose claim is a DIFFERENT account SHALL be rejected
    with the standard `registry:token_invalid` Err."""
    verifier = _verifier()
    client = TestClient(_make_app(verifier=verifier))
    response = client.post(
        path,
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-beta-2026')}"
        },
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "registry:token_invalid"


@pytest.mark.parametrize("path", ROUTE_PATHS)
def test_matching_per_account_token_accepted(path: str) -> None:
    """The token MUST carry the targeted account_id claim (and
    matched) for the route to authorise. The controller is exercised."""
    verifier = _verifier()
    live = _FakeLiveModeController()
    emergency = _FakeEmergencyStopController()
    reconnect = _FakeBrokerReconnectController()
    client = TestClient(
        _make_app(
            verifier=verifier,
            live_mode=live,
            emergency=emergency,
            reconnect=reconnect,
        )
    )
    response = client.post(
        path,
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_enable_invokes_controller_returns_200() -> None:
    verifier = _verifier()
    live = _FakeLiveModeController()
    client = TestClient(_make_app(verifier=verifier, live_mode=live))
    response = client.post(
        "/api/accounts/live-alpha-2026/live-mode/enable",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "account_id": "live-alpha-2026",
        "live_mode": "enabled",
    }
    assert live.enable_calls == [AccountId("live-alpha-2026")]


def test_disable_invokes_controller_returns_200() -> None:
    verifier = _verifier()
    live = _FakeLiveModeController()
    client = TestClient(_make_app(verifier=verifier, live_mode=live))
    response = client.post(
        "/api/accounts/live-alpha-2026/live-mode/disable",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "account_id": "live-alpha-2026",
        "live_mode": "disabled",
    }
    assert live.disable_calls == [AccountId("live-alpha-2026")]


def test_emergency_stop_flips_ks() -> None:
    verifier = _verifier()
    emergency = _FakeEmergencyStopController()
    client = TestClient(
        _make_app(verifier=verifier, emergency=emergency)
    )
    response = client.post(
        "/api/accounts/live-alpha-2026/emergency-stop",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "account_id": "live-alpha-2026",
        "kill_switch": "KILL",
    }
    assert emergency.trigger_calls == [AccountId("live-alpha-2026")]


def test_broker_reconnect_calls_controller() -> None:
    verifier = _verifier()
    reconnect = _FakeBrokerReconnectController()
    client = TestClient(
        _make_app(verifier=verifier, reconnect=reconnect)
    )
    response = client.post(
        "/api/accounts/live-alpha-2026/broker-reconnect",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "account_id": "live-alpha-2026",
        "broker": "reconnected",
    }
    assert reconnect.reconnect_calls == [AccountId("live-alpha-2026")]


# ---------------------------------------------------------------------------
# Controller Err → categorised HTTP response
# ---------------------------------------------------------------------------


def test_enable_failure_surfaces_403() -> None:
    verifier = _verifier()
    live = _FakeLiveModeController(
        enable_response=Err("live:preflight_failed:broker_authenticate")
    )
    client = TestClient(_make_app(verifier=verifier, live_mode=live))
    response = client.post(
        "/api/accounts/live-alpha-2026/live-mode/enable",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 403
    assert response.json()["error"] == "live:preflight_failed:broker_authenticate"


def test_emergency_stop_failure_surfaces_409() -> None:
    verifier = _verifier()
    emergency = _FakeEmergencyStopController(
        trigger_response=Err("safety:already_killed")
    )
    client = TestClient(
        _make_app(verifier=verifier, emergency=emergency)
    )
    response = client.post(
        "/api/accounts/live-alpha-2026/emergency-stop",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "safety:already_killed"


def test_broker_reconnect_failure_surfaces_502() -> None:
    verifier = _verifier()
    reconnect = _FakeBrokerReconnectController(
        reconnect_response=Err("broker:not_authenticated")
    )
    client = TestClient(
        _make_app(verifier=verifier, reconnect=reconnect)
    )
    response = client.post(
        "/api/accounts/live-alpha-2026/broker-reconnect",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 502
    assert response.json()["error"] == "broker:not_authenticated"


# ---------------------------------------------------------------------------
# TC_LIV_009 — SECURITY audit on every authorised action
# ---------------------------------------------------------------------------


def _captured_security_logs(sink: io.StringIO) -> list[dict]:
    lines = sink.getvalue().strip().splitlines()
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("category") == "security":
            out.append(obj)
    return out


@pytest.mark.parametrize(
    "path,expected_event",
    [
        (ROUTE_PATHS[0], "live_mode_enable"),
        (ROUTE_PATHS[1], "live_mode_disable"),
        (ROUTE_PATHS[2], "emergency_stop"),
        (ROUTE_PATHS[3], "broker_reconnect"),
    ],
)
def test_security_log_emitted_with_token_hash(
    path: str, expected_event: str
) -> None:
    sink = io.StringIO()
    configure_logging(level="INFO", json_output=True, stream=sink)
    verifier = _verifier()
    client = TestClient(_make_app(verifier=verifier))
    token = _account_token(verifier, "live-alpha-2026")
    response = client.post(
        path,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    logs = _captured_security_logs(sink)
    matching = [
        log
        for log in logs
        if log.get("payload", {}).get("event") == expected_event
    ]
    assert matching, f"expected {expected_event!r} SECURITY log line"
    payload = matching[-1]["payload"]
    assert payload["account_id"] == "live-alpha-2026"
    assert payload["outcome"] == "ok"
    # token_hash present; raw token SHALL NEVER appear in any log line.
    assert "token_hash" in payload
    haystack = sink.getvalue()
    assert token not in haystack


def test_security_log_records_failed_outcome() -> None:
    sink = io.StringIO()
    configure_logging(level="INFO", json_output=True, stream=sink)
    verifier = _verifier()
    live = _FakeLiveModeController(
        enable_response=Err("live:preflight_failed")
    )
    client = TestClient(_make_app(verifier=verifier, live_mode=live))
    token = _account_token(verifier, "live-alpha-2026")
    response = client.post(
        "/api/accounts/live-alpha-2026/live-mode/enable",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    logs = _captured_security_logs(sink)
    matching = [
        log
        for log in logs
        if log.get("payload", {}).get("event") == "live_mode_enable"
    ]
    assert matching
    payload = matching[-1]["payload"]
    assert payload["outcome"] == "failed"
    assert payload["message"] == "live:preflight_failed"


# ---------------------------------------------------------------------------
# Controller missing → 500
# ---------------------------------------------------------------------------


def test_live_mode_controller_missing_returns_500() -> None:
    verifier = _verifier()
    state = WebappState(token_verifier=verifier)
    # Don't wire the live_mode_controller slot.
    client = TestClient(create_app(state))
    response = client.post(
        "/api/accounts/live-alpha-2026/live-mode/enable",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 500
    assert response.json()["error"] == "webapp:live_mode_controller_missing"


def test_emergency_stop_controller_missing_returns_500() -> None:
    verifier = _verifier()
    state = WebappState(token_verifier=verifier)
    client = TestClient(create_app(state))
    response = client.post(
        "/api/accounts/live-alpha-2026/emergency-stop",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 500
    assert response.json()["error"] == "webapp:emergency_stop_controller_missing"


def test_broker_reconnect_controller_missing_returns_500() -> None:
    verifier = _verifier()
    state = WebappState(token_verifier=verifier)
    client = TestClient(create_app(state))
    response = client.post(
        "/api/accounts/live-alpha-2026/broker-reconnect",
        headers={
            "Authorization": f"Bearer {_account_token(verifier, 'live-alpha-2026')}"
        },
    )
    assert response.status_code == 500
    assert response.json()["error"] == "webapp:broker_reconnect_controller_missing"
