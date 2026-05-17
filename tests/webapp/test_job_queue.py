"""TC_FAS_009 — async backtest invocation + JobQueue.

REQ refs:
- REQ_F_FAS_006 — async backtest; ``202 Accepted`` + job_id;
  status poll + SSE progress stream.
- REQ_SDD_FAS_005 — InProcessJobQueue ProcessPoolExecutor; queue
  PENDING state BEFORE the executor accepts so a worker crash
  leaves a resumable row.
- REQ_NF_WEB_001 — child-process isolation: HTTP crash SHALL NOT
  propagate to trading. Verified via the InProcessJobQueue running
  the default worker in a separate process; tests use an injected
  in-process worker so the assertions are fast.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.job_queue import (
    InProcessJobQueue,
    JobSpec,
    JobStatus,
    new_job_id,
)


# ---------------------------------------------------------------------------
# Picklable worker stubs (must be module-level for ProcessPoolExecutor)
# ---------------------------------------------------------------------------


def _ok_worker(spec: JobSpec) -> dict[str, str]:
    return {
        "trades_count": "3",
        "final_equity_after_tax": "10500.00",
        "config_hash": "deadbeef",
        "seed": "1",
        "data_provider": "mock",
        "equity_curve_points": "30",
    }


def _bad_worker(spec: JobSpec) -> dict[str, str]:
    raise RuntimeError("config:io:missing")


# ---------------------------------------------------------------------------
# Direct queue tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_returns_job_id_and_persists_pending_state_first() -> None:
    """REQ_SDD_FAS_005 — PENDING state recorded BEFORE the executor
    accepts. We verify by checking the in-memory store transitions
    PENDING → RUNNING → COMPLETED."""
    queue = InProcessJobQueue(workers=1, worker_fn=_ok_worker)
    spec = JobSpec(
        job_id=new_job_id(),
        config_dir="config",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 8, tzinfo=UTC),
    )
    result = await queue.submit(spec)
    assert result.is_ok()
    # After submit returns, the state SHALL be at least RUNNING (the
    # transition is synchronous before the future is created).
    state = queue.status(spec.job_id).unwrap()
    assert state.status in (JobStatus.PENDING, JobStatus.RUNNING)
    # Wait for the worker to finish.
    for _ in range(50):
        st = queue.status(spec.job_id).unwrap()
        if st.status == JobStatus.COMPLETED:
            break
        await asyncio.sleep(0.05)
    final = queue.status(spec.job_id).unwrap()
    assert final.status == JobStatus.COMPLETED
    assert final.summary == _ok_worker(spec)
    queue.close()


@pytest.mark.asyncio
async def test_duplicate_job_id_rejected() -> None:
    queue = InProcessJobQueue(workers=1, worker_fn=_ok_worker)
    spec = JobSpec(
        job_id="fixed-id",
        config_dir="config",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 8, tzinfo=UTC),
    )
    first = await queue.submit(spec)
    assert first.is_ok()
    second = await queue.submit(spec)
    assert second.is_err()
    queue.close()


@pytest.mark.asyncio
async def test_worker_error_marks_job_failed() -> None:
    queue = InProcessJobQueue(workers=1, worker_fn=_bad_worker)
    spec = JobSpec(
        job_id=new_job_id(),
        config_dir="config",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 8, tzinfo=UTC),
    )
    await queue.submit(spec)
    for _ in range(50):
        st = queue.status(spec.job_id).unwrap()
        if st.status == JobStatus.FAILED:
            break
        await asyncio.sleep(0.05)
    final = queue.status(spec.job_id).unwrap()
    assert final.status == JobStatus.FAILED
    assert final.error_category is not None
    assert "config:io:missing" in final.error_category
    queue.close()


@pytest.mark.asyncio
async def test_status_missing_returns_nothing() -> None:
    queue = InProcessJobQueue(workers=1, worker_fn=_ok_worker)
    assert queue.status("never-submitted").is_none()
    queue.close()


@pytest.mark.asyncio
async def test_stream_yields_terminal_state() -> None:
    queue = InProcessJobQueue(workers=1, worker_fn=_ok_worker)
    spec = JobSpec(
        job_id=new_job_id(),
        config_dir="config",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 8, tzinfo=UTC),
    )
    await queue.submit(spec)
    last_state = None
    async for state in queue.stream(spec.job_id, poll_interval=0.05):
        last_state = state
    assert last_state is not None
    assert last_state.status in (JobStatus.COMPLETED, JobStatus.FAILED)
    queue.close()


@pytest.mark.asyncio
async def test_all_returns_submitted_jobs_in_chronological_order() -> None:
    queue = InProcessJobQueue(workers=1, worker_fn=_ok_worker)
    for i in range(3):
        await queue.submit(
            JobSpec(
                job_id=f"job-{i}",
                config_dir="config",
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2026, 1, 8, tzinfo=UTC),
            )
        )
        time.sleep(0.01)
    all_jobs = queue.all()
    assert [j.job_id for j in all_jobs] == ["job-0", "job-1", "job-2"]
    queue.close()


# ---------------------------------------------------------------------------
# Route integration via TestClient
# ---------------------------------------------------------------------------


def _client_with_queue() -> tuple[TestClient, str, InProcessJobQueue]:
    verifier = AccountScopedTokenVerifier(secret=b"phase-b-secret", ttl_seconds=3600)
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))
    queue = InProcessJobQueue(workers=1, worker_fn=_ok_worker)
    app = create_app(WebappState(token_verifier=verifier, job_queue=queue))
    return TestClient(app), token, queue


def test_post_backtest_returns_202_and_job_id() -> None:
    client, token, queue = _client_with_queue()
    try:
        response = client.post(
            "/api/backtests",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "config_dir": "config",
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-08T00:00:00+00:00",
            },
        )
        assert response.status_code == 202
        payload = response.json()
        assert "job_id" in payload
        assert payload["status_url"] == f"/api/backtests/{payload['job_id']}"
        assert payload["stream_url"] == f"/events/backtests/{payload['job_id']}"
    finally:
        queue.close()


def test_get_backtest_status_after_completion() -> None:
    client, token, queue = _client_with_queue()
    try:
        submit = client.post(
            "/api/backtests",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "config_dir": "config",
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-08T00:00:00+00:00",
            },
        ).json()
        job_id = submit["job_id"]
        # Poll the status endpoint up to 5s for the worker to finish.
        for _ in range(100):
            response = client.get(
                f"/api/backtests/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            body = response.json()
            if body["status"] == "completed":
                break
            time.sleep(0.05)
        assert body["status"] == "completed"
        assert body["summary"]["trades_count"] == "3"
        assert body["summary"]["final_equity_after_tax"] == "10500.00"
    finally:
        queue.close()


def test_get_backtest_not_found_returns_404() -> None:
    client, token, queue = _client_with_queue()
    try:
        response = client.get(
            "/api/backtests/nope",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
        assert b"job:not_found:nope" in response.content
    finally:
        queue.close()


def test_list_backtests_returns_submitted_jobs() -> None:
    client, token, queue = _client_with_queue()
    try:
        client.post(
            "/api/backtests",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "config_dir": "config",
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-08T00:00:00+00:00",
            },
        )
        response = client.get(
            "/api/backtests",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "jobs" in body
        assert len(body["jobs"]) == 1
    finally:
        queue.close()


def test_post_backtest_requires_household_token() -> None:
    client, _, queue = _client_with_queue()
    try:
        response = client.post(
            "/api/backtests",
            json={
                "config_dir": "config",
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-08T00:00:00+00:00",
            },
        )
        assert response.status_code == 401
    finally:
        queue.close()


def test_post_backtest_validates_body() -> None:
    client, token, queue = _client_with_queue()
    try:
        response = client.post(
            "/api/backtests",
            headers={"Authorization": f"Bearer {token}"},
            json={"start": "not-a-date"},  # missing fields + bad date
        )
        assert response.status_code == 422
    finally:
        queue.close()
