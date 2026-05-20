"""``SqliteBacktestJobRepository`` — CR-004 Phase B persistent
``JobQueue`` (REQ_F_WEB_003 / REQ_F_WEB_009 / REQ_SDD_WEB_005 /
REQ_SDS_WEB_003).

Satisfies the ``trading_system.webui.job_queue.JobQueue`` Protocol.
This backend persists job specs + their lifecycle transitions in
SQLite so submission survives a webui restart (REQ_SDS_WEB_003).
The repository is the system of record; the in-memory thread-pool
worker (``trading_system.webui.workers.thread_worker``) drains
``backtest_job_states`` rows whose latest status is ``PENDING`` and
calls the backtest driver out-of-band.

The route layer never touches this repo directly — it goes through
the ``JobQueue`` Protocol slot wired at server construction. That
keeps the routes module Protocol-shaped per REQ_SDD_WEB_006.

Schema lives in ``persistence/migrations/0006_backtest_jobs.sql``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
)
from trading_system.models.jobs import (
    BacktestJobSpec,
    BacktestJobState,
    JobStatus,
)
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


def _default_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(slots=True)
class SqliteBacktestJobRepository:
    """SQLite-backed persistent ``JobQueue``.

    ``submit`` writes the spec row + the initial ``PENDING``
    transition in a single transaction so a crash between the two
    can't leave a job stranded with no state. ``status`` reads the
    most recent transition row for ``(account_id, job_id)``.

    Workers update state via ``record_transition`` (RUNNING /
    COMPLETED / FAILED). The repo is intentionally agnostic to who
    owns the workers — the in-process thread worker in
    ``webui.workers.thread_worker`` is the default, but a separate
    daemon can drain the same table.
    """

    conn: Connection
    now: Callable[[], datetime] = field(default_factory=lambda: _default_now)

    # ------------------------------------------------------------------
    # JobQueue Protocol surface
    # ------------------------------------------------------------------

    def submit(self, spec: BacktestJobSpec) -> Result[str, str]:
        """Persist ``spec`` + the initial ``PENDING`` transition.

        Duplicate ``(account_id, job_id)`` surfaces as
        ``persistence:integrity:backtest_jobs:duplicate:<job_id>`` —
        callers SHOULD generate fresh ids (the route handler uses
        ``uuid.uuid4().hex``) so duplicates indicate a programmer
        error rather than a retry.
        """
        submitted_at = self.now()
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                """
                INSERT INTO backtest_jobs (
                    account_id, job_id, config_dir,
                    start_ts, end_ts, with_slippage, submitted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.account_id,
                    spec.job_id,
                    spec.config_dir,
                    spec.start.isoformat(),
                    spec.end.isoformat(),
                    1 if spec.with_slippage else 0,
                    submitted_at.isoformat(),
                ),
            )
            self.conn.execute(
                """
                INSERT INTO backtest_job_states (
                    account_id, job_id, transition_seq, status,
                    transitioned_at, error_category, summary_json
                ) VALUES (?, ?, 0, ?, ?, NULL, '{}')
                """,
                (
                    spec.account_id,
                    spec.job_id,
                    JobStatus.PENDING.value,
                    submitted_at.isoformat(),
                ),
            )
            self.conn.commit()
        except IntegrityError as e:
            self.conn.rollback()
            return Err(
                f"persistence:integrity:backtest_jobs:duplicate:{spec.job_id}: {e}"
            )
        except DatabaseError as e:
            self.conn.rollback()
            return Err(f"persistence:corrupt:backtest_jobs:write:{e}")
        return Ok(spec.job_id)

    def status(self, job_id: str) -> Result[Option[BacktestJobState], str]:
        """Return the most recent state for ``job_id`` across every
        account. The route handler scopes by account separately —
        the JobQueue Protocol's ``status`` is account-agnostic for
        operator-tooling convenience (one job id; one lookup)."""
        try:
            cursor = self.conn.execute(
                """
                SELECT j.job_id, j.submitted_at,
                       s.status, s.transitioned_at,
                       s.error_category, s.summary_json,
                       s.transition_seq
                FROM backtest_jobs j
                JOIN backtest_job_states s
                  ON s.account_id = j.account_id
                 AND s.job_id = j.job_id
                WHERE j.job_id = ?
                ORDER BY s.transition_seq DESC
                LIMIT 1
                """,
                (job_id,),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:backtest_job_states:read:{e}")
        if row is None:
            return Ok(Nothing())
        return Ok(Some(_row_to_state(row)))

    def list_for_account(
        self, account_id: str
    ) -> Result[tuple[BacktestJobState, ...], str]:
        """Latest state for every job belonging to ``account_id``,
        sorted by ``submitted_at`` ASC for stable test snapshots."""
        try:
            cursor = self.conn.execute(
                """
                SELECT j.job_id, j.submitted_at,
                       latest.status, latest.transitioned_at,
                       latest.error_category, latest.summary_json,
                       latest.transition_seq
                FROM backtest_jobs j
                JOIN (
                    SELECT account_id, job_id,
                           status, transitioned_at,
                           error_category, summary_json,
                           transition_seq,
                           ROW_NUMBER() OVER (
                               PARTITION BY account_id, job_id
                               ORDER BY transition_seq DESC
                           ) AS rn
                    FROM backtest_job_states
                ) latest
                  ON latest.account_id = j.account_id
                 AND latest.job_id = j.job_id
                 AND latest.rn = 1
                WHERE j.account_id = ?
                ORDER BY j.submitted_at ASC
                """,
                (account_id,),
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:backtest_job_states:read:{e}")
        return Ok(tuple(_row_to_state(row) for row in rows))

    # ------------------------------------------------------------------
    # Worker-facing surface
    # ------------------------------------------------------------------

    def record_transition(
        self,
        *,
        job_id: str,
        status: JobStatus,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
        error_category: str | None = None,
        summary: dict[str, str] | None = None,
    ) -> Result[None, str]:
        """Append one transition row. ``transition_seq`` auto-increments
        per ``(account_id, job_id)``. Workers SHALL call this when
        they pick up a PENDING row (-> RUNNING), when they finish
        (-> COMPLETED), and when they fail (-> FAILED with a
        categorised ``error_category``)."""
        if status in (JobStatus.FAILED,) and not error_category:
            return Err("webui:bad_transition:failed_requires_error_category")
        try:
            cursor = self.conn.execute(
                """
                SELECT COALESCE(MAX(transition_seq), -1) AS prev_seq
                FROM backtest_job_states
                WHERE account_id = ? AND job_id = ?
                """,
                (str(account_id), job_id),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:backtest_job_states:read:{e}")
        if row is None or row["prev_seq"] < 0:
            return Err(f"webui:bad_transition:unknown_job:{job_id}")
        next_seq = int(row["prev_seq"]) + 1
        summary_json = json.dumps(
            dict(sorted((summary or {}).items())),
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                """
                INSERT INTO backtest_job_states (
                    account_id, job_id, transition_seq, status,
                    transitioned_at, error_category, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(account_id),
                    job_id,
                    next_seq,
                    status.value,
                    self.now().isoformat(),
                    error_category,
                    summary_json,
                ),
            )
            self.conn.commit()
        except DatabaseError as e:
            self.conn.rollback()
            return Err(f"persistence:corrupt:backtest_job_states:write:{e}")
        return Ok(None)

    def claim_next_pending(
        self, *, account_id: AccountId = DEFAULT_ACCOUNT_ID
    ) -> Result[Option[BacktestJobSpec], str]:
        """Atomically pick the oldest PENDING job and transition it
        to RUNNING. Used by the in-process worker to drain the queue
        without a separate scheduler. Returns the spec on success so
        the worker can launch the backtest without a second lookup.
        """
        try:
            self.conn.begin_immediate()
            cursor = self.conn.execute(
                """
                SELECT j.job_id, j.config_dir, j.start_ts, j.end_ts,
                       j.with_slippage
                FROM backtest_jobs j
                JOIN (
                    SELECT account_id, job_id,
                           status,
                           ROW_NUMBER() OVER (
                               PARTITION BY account_id, job_id
                               ORDER BY transition_seq DESC
                           ) AS rn
                    FROM backtest_job_states
                ) latest
                  ON latest.account_id = j.account_id
                 AND latest.job_id = j.job_id
                 AND latest.rn = 1
                WHERE j.account_id = ?
                  AND latest.status = ?
                ORDER BY j.submitted_at ASC
                LIMIT 1
                """,
                (str(account_id), JobStatus.PENDING.value),
            )
            row = cursor.fetchone()
            if row is None:
                self.conn.commit()
                return Ok(Nothing())
            job_id = row["job_id"]
            cursor = self.conn.execute(
                """
                SELECT COALESCE(MAX(transition_seq), -1) AS prev_seq
                FROM backtest_job_states
                WHERE account_id = ? AND job_id = ?
                """,
                (str(account_id), job_id),
            )
            seq_row = cursor.fetchone()
            assert seq_row is not None and seq_row["prev_seq"] >= 0
            next_seq = int(seq_row["prev_seq"]) + 1
            self.conn.execute(
                """
                INSERT INTO backtest_job_states (
                    account_id, job_id, transition_seq, status,
                    transitioned_at, error_category, summary_json
                ) VALUES (?, ?, ?, ?, ?, NULL, '{}')
                """,
                (
                    str(account_id),
                    job_id,
                    next_seq,
                    JobStatus.RUNNING.value,
                    self.now().isoformat(),
                ),
            )
            self.conn.commit()
        except DatabaseError as e:
            self.conn.rollback()
            return Err(f"persistence:corrupt:backtest_jobs:claim:{e}")
        return Ok(
            Some(
                BacktestJobSpec(
                    job_id=row["job_id"],
                    config_dir=row["config_dir"],
                    start=datetime.fromisoformat(row["start_ts"]),
                    end=datetime.fromisoformat(row["end_ts"]),
                    with_slippage=bool(row["with_slippage"]),
                    account_id=str(account_id),
                )
            )
        )


# ---------------------------------------------------------------------------
# Row decoding helper
# ---------------------------------------------------------------------------


def _row_to_state(row) -> BacktestJobState:  # type: ignore[no-untyped-def]
    """Materialise one ``backtest_jobs JOIN backtest_job_states`` row
    into the Protocol-typed ``BacktestJobState``. The ``summary_json``
    column is parsed back into a flat ``dict[str, str]``; non-string
    values are coerced via ``str()`` so the Protocol invariant
    holds."""
    submitted_at = datetime.fromisoformat(row["submitted_at"])
    transitioned_at = datetime.fromisoformat(row["transitioned_at"])
    status = JobStatus(row["status"])
    started_at: datetime | None = None
    completed_at: datetime | None = None
    if status in (JobStatus.RUNNING, JobStatus.COMPLETED, JobStatus.FAILED):
        started_at = transitioned_at
    if status in (JobStatus.COMPLETED, JobStatus.FAILED):
        completed_at = transitioned_at
    summary_raw = json.loads(row["summary_json"]) if row["summary_json"] else {}
    summary = {str(k): str(v) for k, v in summary_raw.items()}
    return BacktestJobState(
        job_id=row["job_id"],
        status=status,
        submitted_at=submitted_at,
        started_at=started_at,
        completed_at=completed_at,
        error_category=row["error_category"],
        summary=summary,
    )
