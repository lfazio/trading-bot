"""``SqliteBacktestJobRepository`` tests — CR-004 Phase B
(REQ_F_WEB_003 / REQ_F_WEB_009 / REQ_SDD_WEB_005 / REQ_SDS_WEB_003).

The SQLite backend SHALL satisfy the ``JobQueue`` Protocol so the
route layer's call site stays unchanged whether the operator wires
an in-memory or a persisted queue. Submit + status round-trip,
transitions (PENDING -> RUNNING -> COMPLETED / FAILED), and the
``claim_next_pending`` worker hook are the closed surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

import pytest

from trading_system.models.identifiers import AccountId
from trading_system.models.jobs import (
    BacktestJobSpec,
    BacktestJobState,
    JobStatus,
)
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories import SqliteBacktestJobRepository
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.webui.job_queue import JobQueue


_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "trading_system"
    / "persistence"
    / "migrations"
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Connection]:
    db_path = tmp_path / "test.db"
    connection = Connection.open(db_path).unwrap()
    runner = MigrationRunner(conn=connection, migrations_dir=_MIGRATIONS_DIR)
    runner.run().unwrap()
    yield connection
    connection.close()


def _spec(job_id: str = "job-1", account_id: str = "alpha") -> BacktestJobSpec:
    return BacktestJobSpec(
        job_id=job_id,
        config_dir="/tmp/configs/foo",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 4, 1, tzinfo=UTC),
        with_slippage=False,
        account_id=account_id,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_satisfies_job_queue_protocol(conn: Connection) -> None:
    """REQ_F_WEB_003 — SQLite backend is a drop-in for an in-memory
    queue; the JobQueue Protocol runtime-check SHALL pass."""
    repo = SqliteBacktestJobRepository(conn=conn)
    assert isinstance(repo, JobQueue)


# ---------------------------------------------------------------------------
# submit -> status round-trip
# ---------------------------------------------------------------------------


def test_submit_persists_pending_state(conn: Connection) -> None:
    fixed_now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    repo = SqliteBacktestJobRepository(conn=conn, now=lambda: fixed_now)
    match repo.submit(_spec()):
        case Ok(job_id):
            assert job_id == "job-1"
        case Err(reason):
            pytest.fail(f"submit returned Err: {reason}")
    match repo.status("job-1"):
        case Ok(Some(state)):
            assert isinstance(state, BacktestJobState)
            assert state.job_id == "job-1"
            assert state.status == JobStatus.PENDING
            assert state.submitted_at == fixed_now
            assert state.started_at is None
            assert state.completed_at is None
            assert state.error_category is None
            assert state.summary == {}
        case other:
            pytest.fail(f"unexpected status result: {other!r}")


def test_status_unknown_job_returns_nothing(conn: Connection) -> None:
    repo = SqliteBacktestJobRepository(conn=conn)
    assert repo.status("ghost") == Ok(Nothing())


def test_duplicate_submit_categorised_err(conn: Connection) -> None:
    repo = SqliteBacktestJobRepository(conn=conn)
    repo.submit(_spec()).unwrap()
    match repo.submit(_spec()):
        case Err(reason):
            assert reason.startswith(
                "persistence:integrity:backtest_jobs:duplicate:job-1"
            )
        case other:
            pytest.fail(f"expected Err, got {other!r}")


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def test_running_then_completed_transitions(conn: Connection) -> None:
    submitted = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    started = submitted + timedelta(seconds=2)
    finished = submitted + timedelta(seconds=10)
    clock = iter([submitted, started, finished])
    repo = SqliteBacktestJobRepository(conn=conn, now=lambda: next(clock))
    repo.submit(_spec()).unwrap()
    repo.record_transition(
        job_id="job-1",
        account_id=AccountId("alpha"),
        status=JobStatus.RUNNING,
    ).unwrap()
    repo.record_transition(
        job_id="job-1",
        account_id=AccountId("alpha"),
        status=JobStatus.COMPLETED,
        summary={"net_after_tax_return": "0.123", "trades": "42"},
    ).unwrap()
    match repo.status("job-1"):
        case Ok(Some(state)):
            assert state.status == JobStatus.COMPLETED
            assert state.submitted_at == submitted
            assert state.completed_at == finished
            assert state.summary == {
                "net_after_tax_return": "0.123",
                "trades": "42",
            }
        case other:
            pytest.fail(f"unexpected status: {other!r}")


def test_failed_transition_requires_error_category(conn: Connection) -> None:
    repo = SqliteBacktestJobRepository(conn=conn)
    repo.submit(_spec()).unwrap()
    match repo.record_transition(
        job_id="job-1",
        account_id=AccountId("alpha"),
        status=JobStatus.FAILED,
    ):
        case Err(reason):
            assert reason == "webui:bad_transition:failed_requires_error_category"
        case other:
            pytest.fail(f"expected Err, got {other!r}")


def test_failed_transition_categorises(conn: Connection) -> None:
    repo = SqliteBacktestJobRepository(conn=conn)
    repo.submit(_spec()).unwrap()
    repo.record_transition(
        job_id="job-1",
        account_id=AccountId("alpha"),
        status=JobStatus.FAILED,
        error_category="backtest:diverged",
    ).unwrap()
    state = repo.status("job-1").unwrap().unwrap()
    assert state.status == JobStatus.FAILED
    assert state.error_category == "backtest:diverged"


def test_transition_unknown_job(conn: Connection) -> None:
    repo = SqliteBacktestJobRepository(conn=conn)
    match repo.record_transition(
        job_id="ghost",
        account_id=AccountId("alpha"),
        status=JobStatus.RUNNING,
    ):
        case Err(reason):
            assert reason == "webui:bad_transition:unknown_job:ghost"
        case other:
            pytest.fail(f"expected Err, got {other!r}")


# ---------------------------------------------------------------------------
# list_for_account is sorted + scoped
# ---------------------------------------------------------------------------


def test_list_for_account_sorted_by_submitted_at(conn: Connection) -> None:
    times = [
        datetime(2026, 5, 18, 9, 0, tzinfo=UTC),
        datetime(2026, 5, 18, 10, 0, tzinfo=UTC),
        datetime(2026, 5, 18, 11, 0, tzinfo=UTC),
    ]
    clock = iter(times)
    repo = SqliteBacktestJobRepository(conn=conn, now=lambda: next(clock))
    repo.submit(_spec("a", "alpha")).unwrap()
    repo.submit(_spec("b", "alpha")).unwrap()
    repo.submit(_spec("c", "beta")).unwrap()
    listed = repo.list_for_account("alpha").unwrap()
    assert tuple(s.job_id for s in listed) == ("a", "b")
    assert all(s.status == JobStatus.PENDING for s in listed)


# ---------------------------------------------------------------------------
# Worker hook: claim_next_pending
# ---------------------------------------------------------------------------


def test_claim_next_pending_transitions_to_running(conn: Connection) -> None:
    submitted = datetime(2026, 5, 18, 9, 0, tzinfo=UTC)
    claimed = submitted + timedelta(seconds=5)
    clock = iter([submitted, claimed])
    repo = SqliteBacktestJobRepository(conn=conn, now=lambda: next(clock))
    repo.submit(_spec("job-1", "alpha")).unwrap()
    match repo.claim_next_pending(account_id=AccountId("alpha")):
        case Ok(Some(spec)):
            assert spec.job_id == "job-1"
            assert spec.config_dir == "/tmp/configs/foo"
            assert spec.start == datetime(2026, 1, 1, tzinfo=UTC)
        case other:
            pytest.fail(f"expected claimed spec, got {other!r}")
    state = repo.status("job-1").unwrap().unwrap()
    assert state.status == JobStatus.RUNNING
    assert state.started_at == claimed


def test_claim_next_pending_empty_returns_nothing(conn: Connection) -> None:
    repo = SqliteBacktestJobRepository(conn=conn)
    assert repo.claim_next_pending(
        account_id=AccountId("alpha")
    ) == Ok(Nothing())


def test_claim_next_pending_skips_running(conn: Connection) -> None:
    times = [
        datetime(2026, 5, 18, 9, 0, tzinfo=UTC),
        datetime(2026, 5, 18, 9, 5, tzinfo=UTC),
    ]
    clock = iter(times)
    repo = SqliteBacktestJobRepository(conn=conn, now=lambda: next(clock))
    repo.submit(_spec("job-1", "alpha")).unwrap()
    repo.claim_next_pending(account_id=AccountId("alpha")).unwrap()
    # Second claim must not re-pick the same RUNNING job.
    assert repo.claim_next_pending(
        account_id=AccountId("alpha")
    ) == Ok(Nothing())
