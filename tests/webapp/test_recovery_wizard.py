"""Tests for the CR-019 kill-switch recovery wizard.

REQ refs:
- REQ_F_WEB2_007 — wizard walks the operator through the
  RecoveryConditions, displays current state + last trigger,
  accepts a fresh operator token, refuses to submit when any
  condition is unchecked.
- TC_KS_WEB2_001 — ACTIVE -> informational page; KILL -> wizard;
  fully-checked POST clears to ACTIVE.
- TC_KS_WEB2_002 — POST with any condition unset returns 400 +
  `safety:recovery_conditions_unmet`; submit is DOM-disabled.
"""

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


_SECRET = b"recovery-wizard-secret"


@dataclass(slots=True)
class _FakeRecoveryGate:
    """Stand-in for the real StateManager that records every
    request_recovery call + emits a configurable result."""

    ks_state: str = "KILL"
    trigger: str | None = "drawdown_breach"
    accept: bool = True
    calls: list[dict[str, Any]] = field(default_factory=list)

    def state(self) -> str:
        return self.ks_state

    def last_trigger(self) -> str | None:
        return self.trigger

    def request_recovery(
        self,
        *,
        token: str,
        drawdown_recovered: bool,
        integrity_restored: bool,
        backtests_stable: bool,
        at: datetime,
    ):
        self.calls.append(
            {
                "token": token,
                "drawdown_recovered": drawdown_recovered,
                "integrity_restored": integrity_restored,
                "backtests_stable": backtests_stable,
                "at": at,
            }
        )
        if not self.accept:
            return Err("safety:invalid_operator_token")
        self.ks_state = "ACTIVE"
        self.trigger = None
        return Ok(None)


def _client(*, gate: _FakeRecoveryGate | None = None):
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    state = WebappState(token_verifier=verifier, recovery_gate=gate)
    return TestClient(create_app(state)), verifier


def _token(verifier):
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC))


# ---------------------------------------------------------------------------
# GET — informational page vs wizard
# ---------------------------------------------------------------------------


def test_recovery_redirects_unauth_to_login() -> None:
    client, _ = _client(gate=_FakeRecoveryGate(ks_state="KILL"))
    response = client.get("/operator/recovery", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_recovery_renders_informational_when_state_active() -> None:
    """TC_KS_WEB2_001 — ACTIVE state SHALL show informational page,
    no wizard form."""
    gate = _FakeRecoveryGate(ks_state="ACTIVE", trigger=None)
    client, verifier = _client(gate=gate)
    response = client.get(
        "/operator/recovery",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    )
    assert response.status_code == 200
    body = response.text
    assert "No recovery needed" in body
    # The form SHALL NOT be present in this branch.
    assert 'id="recovery-form"' not in body


def test_recovery_renders_wizard_when_state_kill() -> None:
    """TC_KS_WEB2_001 — KILL state SHALL render the wizard with
    three checkboxes + an operator-token input."""
    gate = _FakeRecoveryGate(ks_state="KILL", trigger="drawdown_breach")
    client, verifier = _client(gate=gate)
    response = client.get(
        "/operator/recovery",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    )
    body = response.text
    assert 'id="recovery-form"' in body
    assert 'name="drawdown_recovered"' in body
    assert 'name="integrity_restored"' in body
    assert 'name="backtests_stable"' in body
    assert 'name="operator_token"' in body
    # Submit SHALL be DOM-disabled initially (TC_KS_WEB2_002).
    assert (
        'id="recovery-submit" disabled' in body
        or 'disabled\n' in body  # robust against attr-order variation
    )
    # Last trigger surfaced.
    assert "drawdown_breach" in body


def test_recovery_renders_informational_when_no_gate_wired() -> None:
    """When operators forget to wire app.state.recovery_gate the
    view SHALL still render (informational page) instead of 500."""
    client, verifier = _client(gate=None)
    response = client.get(
        "/operator/recovery",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    )
    assert response.status_code == 200
    assert "No recovery needed" in response.text


# ---------------------------------------------------------------------------
# POST — server-side double-check
# ---------------------------------------------------------------------------


def test_post_recovery_rejects_unchecked_conditions_with_categorised_err() -> None:
    """TC_KS_WEB2_002 — POST with any RecoveryConditions checkbox
    unset SHALL return 400 + the categorised
    `safety:recovery_conditions_unmet` Err."""
    gate = _FakeRecoveryGate(ks_state="KILL")
    client, verifier = _client(gate=gate)
    response = client.post(
        "/operator/recovery",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "drawdown_recovered": "on",
            # integrity_restored intentionally omitted.
            "backtests_stable": "on",
            "operator_token": "deadbeef",
        },
    )
    assert response.status_code == 400
    assert "safety:recovery_conditions_unmet" in response.text
    # Gate was NOT called.
    assert gate.calls == []


def test_post_recovery_with_all_conditions_clears_kill_switch() -> None:
    """TC_KS_WEB2_001 — POST with all three boxes checked + a
    token SHALL invoke gate.request_recovery and transition to
    ACTIVE."""
    gate = _FakeRecoveryGate(ks_state="KILL", accept=True)
    client, verifier = _client(gate=gate)
    response = client.post(
        "/operator/recovery",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "drawdown_recovered": "on",
            "integrity_restored": "on",
            "backtests_stable": "on",
            "operator_token": "deadbeef",
        },
    )
    assert response.status_code == 200
    # Gate was called once with all conditions True.
    assert len(gate.calls) == 1
    call = gate.calls[0]
    assert call["drawdown_recovered"] is True
    assert call["integrity_restored"] is True
    assert call["backtests_stable"] is True
    assert call["token"] == "deadbeef"
    # State flipped to ACTIVE.
    assert gate.ks_state == "ACTIVE"
    # The page SHALL show the informational "no recovery needed"
    # state since we are now ACTIVE.
    assert "No recovery needed" in response.text


def test_post_recovery_propagates_gate_err_to_banner() -> None:
    """An Err from the gate (e.g., invalid operator token) SHALL
    surface as the banner; the kill-switch state SHALL NOT
    change."""
    gate = _FakeRecoveryGate(ks_state="KILL", accept=False)
    client, verifier = _client(gate=gate)
    response = client.post(
        "/operator/recovery",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "drawdown_recovered": "on",
            "integrity_restored": "on",
            "backtests_stable": "on",
            "operator_token": "deadbeef",
        },
    )
    assert response.status_code == 400
    assert "safety:invalid_operator_token" in response.text
    assert gate.ks_state == "KILL"


def test_post_recovery_requires_auth() -> None:
    gate = _FakeRecoveryGate(ks_state="KILL")
    client, _ = _client(gate=gate)
    response = client.post(
        "/operator/recovery",
        data={
            "drawdown_recovered": "on",
            "integrity_restored": "on",
            "backtests_stable": "on",
            "operator_token": "deadbeef",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_post_recovery_no_gate_returns_categorised_503() -> None:
    """If the operator hits POST without a wired gate the response
    SHALL be 503 + a categorised Err so they know to wire it."""
    client, verifier = _client(gate=None)
    response = client.post(
        "/operator/recovery",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "drawdown_recovered": "on",
            "integrity_restored": "on",
            "backtests_stable": "on",
            "operator_token": "deadbeef",
        },
    )
    assert response.status_code == 503
    assert "safety:no_recovery_gate_wired" in response.text


# ---------------------------------------------------------------------------
# DOM-level guards (TC_KS_WEB2_002)
# ---------------------------------------------------------------------------


def test_wizard_submit_is_dom_disabled_initially() -> None:
    """The submit button SHALL render with the HTML `disabled`
    attribute so a JS-disabled browser cannot accidentally
    submit an incomplete form."""
    gate = _FakeRecoveryGate(ks_state="KILL")
    client, verifier = _client(gate=gate)
    body = client.get(
        "/operator/recovery",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    ).text
    # Pin the disabled attr on the recovery-submit button.
    import re

    match = re.search(r'<button[^>]+id="recovery-submit"[^>]*>', body)
    assert match is not None
    assert "disabled" in match.group(0)


def test_wizard_dialog_carries_modal_a11y_markers() -> None:
    """REQ_NF_WEB2_003 + REQ_SDD_WEB2_007 — the wizard SHALL be a
    role=dialog with aria-modal + aria-labelledby + the
    data-close-on-esc opt-in."""
    gate = _FakeRecoveryGate(ks_state="KILL")
    client, verifier = _client(gate=gate)
    body = client.get(
        "/operator/recovery",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    ).text
    assert 'role="dialog"' in body
    assert 'aria-modal="true"' in body
    assert 'aria-labelledby="recovery-heading"' in body
    assert 'data-close-on-esc="true"' in body
