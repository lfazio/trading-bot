"""HTMX jobs-view tests.

Three routes:
- ``GET /jobs`` — full page; redirects to ``/login`` when unauth.
- ``GET /jobs/partial`` — HTMX swap fragment.
- ``POST /jobs/submit`` — form-data → enqueue → re-render fragment.

These tests use a synchronous worker stub so the queue completes
jobs deterministically without spawning a real ProcessPoolExecutor.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.result import Err, Nothing, Ok, Option
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.job_queue import JobSpec, JobState, JobStatus


class _StubQueue:
    """Synchronous JobQueue stub — submit appends a COMPLETED state
    immediately so the HTML render sees the row on the next paint."""

    def __init__(self) -> None:
        self.states: dict[str, JobState] = {}

    async def submit(self, spec: JobSpec):  # noqa: D401 — Protocol shape
        if spec.job_id in self.states:
            return Err(f"job:duplicate_id:{spec.job_id}")
        now = datetime.now(UTC)
        self.states[spec.job_id] = JobState(
            job_id=spec.job_id,
            status=JobStatus.COMPLETED,
            submitted_at=now,
            started_at=now,
            completed_at=now + timedelta(milliseconds=1),
            summary={"trades_count": "3", "final_equity_after_tax": "10500.00"},
        )
        return Ok(spec.job_id)

    def status(self, job_id: str) -> Option[JobState]:
        state = self.states.get(job_id)
        if state is None:
            return Nothing()
        from trading_system.result import Some

        return Some(state)

    def all(self) -> tuple[JobState, ...]:
        return tuple(sorted(self.states.values(), key=lambda s: s.submitted_at))

    async def stream(self, job_id: str):  # pragma: no cover — not used
        state = self.states.get(job_id)
        if state is not None:
            yield state

    def close(self) -> None:
        pass


def _client() -> tuple[TestClient, str, _StubQueue]:
    verifier = AccountScopedTokenVerifier(
        secret=b"jobs-view-test", ttl_seconds=3600
    )
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))
    queue = _StubQueue()
    app = create_app(
        WebappState(token_verifier=verifier, job_queue=queue)
    )
    return TestClient(app), token, queue


# ---------------------------------------------------------------------------
# GET /jobs
# ---------------------------------------------------------------------------


def test_jobs_redirects_to_login_when_unauth() -> None:
    client, _, _ = _client()
    response = client.get("/jobs", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_jobs_renders_empty_state() -> None:
    client, token, _ = _client()
    response = client.get(
        "/jobs", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.text
    assert response.headers["content-type"].startswith("text/html")
    assert 'hx-post="/jobs/submit"' in body
    assert 'hx-get="/jobs/partial"' in body
    assert "no runs submitted yet" in body
    # Nav link wired into the chrome.
    assert 'href="/jobs"' in body


def test_jobs_renders_existing_jobs() -> None:
    client, token, queue = _client()
    now = datetime(2026, 5, 19, 9, 0, tzinfo=UTC)
    queue.states["abcdef0123456789"] = JobState(
        job_id="abcdef0123456789",
        status=JobStatus.COMPLETED,
        submitted_at=now,
        started_at=now,
        completed_at=now + timedelta(seconds=2),
        summary={"trades_count": "5", "final_equity_after_tax": "11200.00"},
    )
    response = client.get(
        "/jobs", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.text
    assert "abcdef012345" in body  # truncated id rendered
    assert "<strong>5</strong>" in body
    assert "11200.00" in body


# ---------------------------------------------------------------------------
# GET /jobs/partial
# ---------------------------------------------------------------------------


def test_jobs_partial_requires_auth() -> None:
    client, _, _ = _client()
    response = client.get("/jobs/partial")
    assert response.status_code == 401
    assert response.json()["detail"] == "registry:token_invalid"


def test_jobs_partial_returns_table_fragment() -> None:
    client, token, _ = _client()
    response = client.get(
        "/jobs/partial", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.text
    # Fragment SHALL NOT carry the full HTML chrome.
    assert "<!doctype html>" not in body.lower()
    assert "<table" in body
    assert "no runs submitted yet" in body


# ---------------------------------------------------------------------------
# POST /jobs/submit
# ---------------------------------------------------------------------------


def test_submit_form_enqueues_and_returns_partial() -> None:
    client, token, queue = _client()
    response = client.post(
        "/jobs/submit",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "config_dir": "config",
            "start": "2024-01-02T00:00:00+00:00",
            "end": "2024-12-31T00:00:00+00:00",
            "with_slippage": "on",
        },
    )
    assert response.status_code == 200
    body = response.text
    assert "<table" in body
    assert "completed" in body  # stub finishes inline
    assert "<strong>3</strong>" in body  # trades_count rendered
    # Queue actually received the spec with the parsed datetimes.
    assert len(queue.states) == 1
    state = next(iter(queue.states.values()))
    assert state.status == JobStatus.COMPLETED


def test_submit_form_rejects_bad_iso_date() -> None:
    client, token, queue = _client()
    response = client.post(
        "/jobs/submit",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "config_dir": "config",
            "start": "not-a-date",
            "end": "2024-12-31T00:00:00+00:00",
        },
    )
    assert response.status_code == 200
    body = response.text
    assert "webapp:bad_form:iso_datetime" in body
    assert queue.states == {}


def test_submit_form_unauth() -> None:
    client, _, _ = _client()
    response = client.post(
        "/jobs/submit",
        data={
            "config_dir": "config",
            "start": "2024-01-02T00:00:00+00:00",
            "end": "2024-12-31T00:00:00+00:00",
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "registry:token_invalid"


# ---------------------------------------------------------------------------
# Nav surface
# ---------------------------------------------------------------------------


def test_job_detail_redirects_when_unauth() -> None:
    client, _, _ = _client()
    # Need a known job_id but the redirect happens before lookup.
    response = client.get("/jobs/anything", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_job_detail_renders_job_page() -> None:
    client, token, queue = _client()
    now = datetime(2026, 5, 19, 10, 0, tzinfo=UTC)
    queue.states["abc123"] = JobState(
        job_id="abc123",
        status=JobStatus.COMPLETED,
        submitted_at=now,
        started_at=now,
        completed_at=now + timedelta(seconds=4),
        summary={"trades_count": "7", "final_equity_after_tax": "11500"},
    )
    response = client.get(
        "/jobs/abc123", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.text
    assert "abc123" in body
    assert "completed" in body
    assert 'sse-connect="/events/backtests/abc123"' in body
    # Stat-grid renders the summary values.
    assert "11500" in body
    assert "trades count" in body  # underscore replaced for human display


def test_job_detail_unknown_id_returns_404() -> None:
    client, token, _ = _client()
    response = client.get(
        "/jobs/ghost", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404
    assert "Run not found" in response.text
    assert "ghost" in response.text


def test_jobs_partial_does_not_shadow_detail() -> None:
    """Route-ordering guard — ``/jobs/partial`` SHALL hit the
    partial handler, not the catch-all detail route. If a future
    refactor inverts the registration order this test fires."""
    client, token, _ = _client()
    response = client.get(
        "/jobs/partial", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    # Fragment shape, not the full page chrome.
    assert "<!doctype html>" not in response.text.lower()


def test_dashboard_carries_jobs_nav_link() -> None:
    """The base.html nav block SHALL link to /jobs so operators can
    reach the page from any chrome-rendered view."""
    client, token, _ = _client()
    response = client.get("/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert 'href="/jobs"' in response.text


# Avoid an unused-import lint nit; asyncio is used by Protocol shape.
_ = asyncio
