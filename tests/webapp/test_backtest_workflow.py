"""Tests for CR-019 backtest workflow polish (REQ_F_WEB2_004).

Covers: universe selector + range presets + rerun pre-fill.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.job_queue import InProcessJobQueue


_SECRET = b"backtest-workflow-secret"


def _client():
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    queue = InProcessJobQueue(workers=1)
    state = WebappState(token_verifier=verifier, job_queue=queue)
    return TestClient(create_app(state)), verifier


def _token(verifier):
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC))


def test_jobs_page_renders_universe_selector() -> None:
    client, verifier = _client()
    body = client.get(
        "/jobs", headers={"Authorization": f"Bearer {_token(verifier)}"}
    ).text
    assert 'name="universe"' in body
    assert "eu-dividend-starter" in body
    assert "cac40" in body


def test_jobs_page_renders_range_preset_buttons() -> None:
    client, verifier = _client()
    body = client.get(
        "/jobs", headers={"Authorization": f"Bearer {_token(verifier)}"}
    ).text
    # 5 documented presets.
    for key in ("ytd", "last-30d", "last-90d", "last-year", "2024"):
        assert f'data-range="{key}"' in body
    # JS preset wiring is inlined.
    assert "applyRange" in body


def test_jobs_page_prefills_form_from_query_params() -> None:
    """REQ_F_WEB2_004 — the form fields SHALL accept rerun
    pre-fill via query-string parameters."""
    client, verifier = _client()
    url = (
        "/jobs?config_dir=other-config&universe=cac40"
        "&start=2024-06-01T00:00:00%2B00:00&end=2024-06-30T00:00:00%2B00:00"
        "&with_slippage=on"
    )
    body = client.get(
        url, headers={"Authorization": f"Bearer {_token(verifier)}"}
    ).text
    assert 'value="other-config"' in body
    assert 'value="2024-06-01T00:00:00+00:00"' in body
    assert 'value="2024-06-30T00:00:00+00:00"' in body
    # Universe selector has cac40 as selected.
    assert 'value="cac40" selected' in body
    # Slippage checkbox checked.
    assert "checked" in body


def test_jobs_submit_rejects_unknown_universe() -> None:
    client, verifier = _client()
    response = client.post(
        "/jobs/submit",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "config_dir": "config",
            "start": "2024-01-02T00:00:00+00:00",
            "end": "2024-12-31T00:00:00+00:00",
            "universe": "wat",
        },
    )
    # The handler renders the table partial WITH the error banner;
    # the partial includes the alert message.
    assert response.status_code == 200
    assert "unknown_universe" in response.text


def test_jobs_submit_rejects_start_after_end() -> None:
    client, verifier = _client()
    response = client.post(
        "/jobs/submit",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
        data={
            "config_dir": "config",
            "start": "2024-12-31T00:00:00+00:00",
            "end": "2024-01-02T00:00:00+00:00",
            "universe": "eu-dividend-starter",
        },
    )
    assert response.status_code == 200
    assert "start_after_end" in response.text


def test_jobs_submit_records_prefill_so_rerun_button_renders() -> None:
    """REQ_F_WEB2_004 — after a successful submit the table SHALL
    render a "Rerun" button on the row whose href pre-fills the
    form with the prior inputs."""
    client, verifier = _client()
    token = _token(verifier)
    # Submit a run.
    response = client.post(
        "/jobs/submit",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "config_dir": "config",
            "start": "2024-01-02T00:00:00+00:00",
            "end": "2024-12-31T00:00:00+00:00",
            "universe": "cac40",
            "with_slippage": "on",
        },
    )
    assert response.status_code == 200
    body = response.text
    # The rerun anchor SHALL appear AND its href SHALL carry the
    # form inputs (URL-encoded).
    assert "/jobs?config_dir=config" in body
    assert "universe=cac40" in body
    assert "with_slippage=on" in body
