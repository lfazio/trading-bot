"""``JobQueue`` Protocol for the stdlib webui's async backtest path.

REQ refs:
- REQ_F_WEB_003 — backtest-invocation endpoint enqueues a job.
- REQ_F_WEB_009 — submission returns immediately with a job_id;
  result via separate lookup.
- REQ_SDD_WEB_005 — ``JobQueue.submit(spec)`` returns the job_id
  synchronously; persistence is the system of record for job
  state (REQ_F_WEB_010).
- REQ_SDS_WEB_003 — jobs run outside the HTTP request thread;
  child-process isolation preserves REQ_NF_WEB_001 (HTTP crash
  SHALL NOT propagate to trading).

The stdlib webui is intentionally Protocol-shaped — concrete
implementations (in-process thread queue, ProcessPoolExecutor,
CR-008-backed persistent queue) wire at the deploy boundary.
Operators who want a richer async surface graduate to the
CR-017 FastAPI webapp's ``InProcessJobQueue``.

Data carriers (``BacktestJobSpec``, ``BacktestJobState``,
``JobStatus``) live in ``trading_system.models.jobs`` so neither
the webui nor the persistence layer imports the other — both
sides depend on ``models/`` and satisfy the Protocol structurally.
This module exposes them as re-exports so callers can pretend the
Protocol surface is self-contained.

The route layer reaches the queue through ``app.state.job_queue``
(or a closure-captured reference) so the route stays free of an
import on any concrete implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from trading_system.models.jobs import (
    BacktestJobSpec,
    BacktestJobState,
    JobStatus,
)
from trading_system.result import Option, Result

__all__ = [
    "BacktestJobSpec",
    "BacktestJobState",
    "JobQueue",
    "JobStatus",
]


@runtime_checkable
class JobQueue(Protocol):
    """Storage + dispatch surface for async backtest jobs.

    Implementations SHALL satisfy:

    - ``submit(spec)`` persists the job header in the PENDING
      state BEFORE the worker accepts the future (so a worker
      crash leaves a resumable row). Returns the job_id on
      success; categorised Err otherwise (e.g.,
      ``persistence:integrity:backtest_jobs:duplicate:<id>``).
    - ``status(job_id)`` returns ``Ok(Some(state))`` for known
      jobs, ``Ok(Nothing())`` for unknown ones.
    - ``list_for_account(account_id)`` returns every job
      submitted under the account, sorted by ``submitted_at``
      ascending so the HTTP envelope's chronology is stable.
    """

    def submit(self, spec: BacktestJobSpec) -> Result[str, str]: ...

    def status(self, job_id: str) -> Result[Option[BacktestJobState], str]: ...

    def list_for_account(
        self, account_id: str
    ) -> Result[tuple[BacktestJobState, ...], str]: ...
