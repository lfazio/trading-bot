"""Async-backtest route tests — CR-004 Phase B
(REQ_F_WEB_003 / REQ_F_WEB_009 / REQ_SDD_WEB_005 / REQ_SDS_WEB_003).

The route layer:
- POST /accounts/<aid>/backtests — submit a spec, expect 202 + job_id.
- GET  /accounts/<aid>/backtests/<job_id> — read the latest state.

Both endpoints SHALL require the household auth claim
(REQ_F_WEB_005). The submit endpoint SHALL validate the JSON body
shape AND return a closed Err category on each failure mode so
operators can pattern-match the error surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.jobs import (
    BacktestJobSpec,
    BacktestJobState,
    JobStatus,
)
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.webui.auth import WebAuth
from trading_system.webui.routes.backtests import (
    build_status_handler,
    build_submit_handler,
)
from trading_system.webui.server import Request


_NOW = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)


def _auth() -> tuple[WebAuth, AccountScopedTokenVerifier]:
    v = AccountScopedTokenVerifier(
        secret=b"shh", ttl_seconds=300, _clock=lambda: _NOW
    )
    return WebAuth(verifier=v), v


def _household_token(v: AccountScopedTokenVerifier) -> str:
    return v.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)


@dataclass(slots=True)
class _StubQueue:
    """In-memory JobQueue stub for route tests."""

    submitted: list[BacktestJobSpec] = field(default_factory=list)
    states: dict[str, BacktestJobState] = field(default_factory=dict)
    submit_outcome: Result[str, str] | None = None

    def submit(self, spec: BacktestJobSpec) -> Result[str, str]:
        self.submitted.append(spec)
        if self.submit_outcome is not None:
            return self.submit_outcome
        self.states[spec.job_id] = BacktestJobState(
            job_id=spec.job_id,
            status=JobStatus.PENDING,
            submitted_at=_NOW,
        )
        return Ok(spec.job_id)

    def status(self, job_id: str) -> Result[Option[BacktestJobState], str]:
        state = self.states.get(job_id)
        if state is None:
            return Ok(Nothing())
        return Ok(Some(state))

    def list_for_account(
        self, account_id: str
    ) -> Result[tuple[BacktestJobState, ...], str]:
        del account_id  # stub returns every known job
        return Ok(tuple(self.states.values()))


def _body(**overrides: object) -> bytes:
    body: dict[str, object] = {
        "config_dir": "/configs/foo",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-04-01T00:00:00+00:00",
        "with_slippage": False,
    }
    body.update(overrides)
    return json.dumps(body).encode("utf-8")


# ---------------------------------------------------------------------------
# Submit handler
# ---------------------------------------------------------------------------


def test_submit_returns_202_with_job_id() -> None:
    auth, v = _auth()
    queue = _StubQueue()
    handler = build_submit_handler(
        auth=auth,
        queue=queue,
        id_generator=lambda: "deterministic-id",
    )
    response = handler(
        Request(
            method="POST",
            path="/accounts/alpha/backtests",
            headers={"Authorization": f"Bearer {_household_token(v)}"},
            body=_body(),
        )
    )
    assert response.status_code == 202
    payload = json.loads(response.body)
    assert payload["job_id"] == "deterministic-id"
    assert (
        payload["status_url"]
        == "/accounts/alpha/backtests/deterministic-id"
    )
    assert len(queue.submitted) == 1
    submitted = queue.submitted[0]
    assert submitted.job_id == "deterministic-id"
    assert submitted.config_dir == "/configs/foo"
    assert submitted.account_id == "alpha"


def test_submit_unauthenticated() -> None:
    auth, _ = _auth()
    queue = _StubQueue()
    handler = build_submit_handler(auth=auth, queue=queue)
    response = handler(
        Request(
            method="POST",
            path="/accounts/alpha/backtests",
            headers={},
            body=_body(),
        )
    )
    assert response.status_code == 401
    assert json.loads(response.body) == {"error": "registry:token_invalid"}
    assert queue.submitted == []


def test_submit_bad_path() -> None:
    auth, v = _auth()
    handler = build_submit_handler(auth=auth, queue=_StubQueue())
    response = handler(
        Request(
            method="POST",
            path="/wrong/shape",
            headers={"Authorization": f"Bearer {_household_token(v)}"},
            body=_body(),
        )
    )
    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "webui:bad_path"}


def test_submit_bad_body_config_dir() -> None:
    auth, v = _auth()
    handler = build_submit_handler(auth=auth, queue=_StubQueue())
    response = handler(
        Request(
            method="POST",
            path="/accounts/alpha/backtests",
            headers={"Authorization": f"Bearer {_household_token(v)}"},
            body=_body(config_dir=""),
        )
    )
    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": "webui:bad_request_body:config_dir"
    }


def test_submit_bad_body_iso_datetime() -> None:
    auth, v = _auth()
    handler = build_submit_handler(auth=auth, queue=_StubQueue())
    response = handler(
        Request(
            method="POST",
            path="/accounts/alpha/backtests",
            headers={"Authorization": f"Bearer {_household_token(v)}"},
            body=_body(start="not-a-date"),
        )
    )
    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": "webui:bad_request_body:iso_datetime"
    }


def test_submit_queue_err_surfaces_409() -> None:
    auth, v = _auth()
    queue = _StubQueue(
        submit_outcome=Err(
            "persistence:integrity:backtest_jobs:duplicate:foo"
        )
    )
    handler = build_submit_handler(auth=auth, queue=queue)
    response = handler(
        Request(
            method="POST",
            path="/accounts/alpha/backtests",
            headers={"Authorization": f"Bearer {_household_token(v)}"},
            body=_body(),
        )
    )
    assert response.status_code == 409
    assert json.loads(response.body) == {
        "error": "persistence:integrity:backtest_jobs:duplicate:foo"
    }


def test_submit_rejects_wrong_method() -> None:
    auth, v = _auth()
    handler = build_submit_handler(auth=auth, queue=_StubQueue())
    response = handler(
        Request(
            method="GET",
            path="/accounts/alpha/backtests",
            headers={"Authorization": f"Bearer {_household_token(v)}"},
        )
    )
    assert response.status_code == 405


# ---------------------------------------------------------------------------
# Status handler
# ---------------------------------------------------------------------------


def test_status_returns_state_payload() -> None:
    auth, v = _auth()
    queue = _StubQueue()
    queue.states["job-1"] = BacktestJobState(
        job_id="job-1",
        status=JobStatus.COMPLETED,
        submitted_at=_NOW,
        started_at=_NOW,
        completed_at=_NOW,
        summary={"net_after_tax_return": "0.123"},
    )
    handler = build_status_handler(auth=auth, queue=queue)
    response = handler(
        Request(
            method="GET",
            path="/accounts/alpha/backtests/job-1",
            headers={"Authorization": f"Bearer {_household_token(v)}"},
        )
    )
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["job_id"] == "job-1"
    assert payload["status"] == "completed"
    assert payload["summary"] == {"net_after_tax_return": "0.123"}


def test_status_unknown_job_returns_404() -> None:
    auth, v = _auth()
    handler = build_status_handler(auth=auth, queue=_StubQueue())
    response = handler(
        Request(
            method="GET",
            path="/accounts/alpha/backtests/ghost",
            headers={"Authorization": f"Bearer {_household_token(v)}"},
        )
    )
    assert response.status_code == 404
    assert json.loads(response.body) == {"error": "webui:job_not_found:ghost"}


def test_status_canonical_replay_byte_identical() -> None:
    """REQ_NF_WEB_002 — equal inputs ⇒ equal bytes."""
    auth, v = _auth()
    queue = _StubQueue()
    queue.states["job-1"] = BacktestJobState(
        job_id="job-1",
        status=JobStatus.COMPLETED,
        submitted_at=_NOW,
        started_at=_NOW,
        completed_at=_NOW,
        summary={"trades": "42"},
    )
    handler = build_status_handler(auth=auth, queue=queue)
    request = Request(
        method="GET",
        path="/accounts/alpha/backtests/job-1",
        headers={"Authorization": f"Bearer {_household_token(v)}"},
    )
    first = handler(request)
    second = handler(request)
    assert first.body == second.body
