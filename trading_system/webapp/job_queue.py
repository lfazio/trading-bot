"""Async backtest JobQueue + ProcessPoolExecutor wiring.

REQ refs:
- REQ_F_FAS_006 — async backtest invocation via JobQueue;
  ``202 Accepted`` + ``job_id``; SSE-streamed progress.
- REQ_SDD_FAS_005 — ``InProcessJobQueue`` runs jobs in
  ``concurrent.futures.ProcessPoolExecutor``; queue_pending
  registers the job header BEFORE the executor accepts so a
  worker crash leaves a resumable row; ``stream()`` combines
  polling + an ``asyncio.Event`` so newly-submitted jobs notify
  their watchers immediately.
- REQ_NF_WEB_001 — HTTP crash SHALL NOT propagate to the engine;
  jobs run in a child process pool isolated from the FastAPI
  worker.

The Phase-B v1 stores state in-memory; CR-008's
``BacktestResultRepository`` slot is the live-mode persistence
target. The Protocol surface stays stable so a follow-up SQLite
backend drops in without touching the routes.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from trading_system.result import Err, Nothing, Ok, Option, Result, Some


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobSpec:
    """Picklable backtest invocation spec — every field is a
    primitive so ``ProcessPoolExecutor.submit`` can serialise it.

    The runtime job invokes ``trading_system.main.run`` with these
    args and returns a ``JobSummary`` mapping (kept small + JSON-safe
    so the SSE channel can serialise it directly).
    """

    job_id: str
    config_dir: str
    start: datetime
    end: datetime
    with_slippage: bool = False
    account_id: str = "default"


@dataclass(frozen=True, slots=True)
class JobState:
    """Snapshot of a single job's state. Returned by ``status`` and
    yielded by ``stream``."""

    job_id: str
    status: JobStatus
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_category: str | None = None
    summary: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class JobQueue(Protocol):
    async def submit(self, spec: JobSpec) -> Result[str, str]: ...
    def status(self, job_id: str) -> Option[JobState]: ...
    def all(self) -> tuple[JobState, ...]: ...
    def stream(self, job_id: str) -> AsyncIterator[JobState]: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# In-process implementation
# ---------------------------------------------------------------------------


def _default_worker(spec: JobSpec) -> dict[str, str]:
    """Top-level (picklable) worker entry. Invokes
    ``trading_system.main.run`` + emits the CR-016 report bundle so
    the webapp's reports panel can render the equity curve inline."""
    from trading_system.analytics.report import write_report
    from trading_system.main import run

    res = run(
        config_dir=Path(spec.config_dir),
        start=spec.start,
        end=spec.end,
        use_slippage=spec.with_slippage,
    )
    if isinstance(res, Err):
        raise RuntimeError(res.error)
    outcome = res.value

    # Emit the 5-file report directory keyed on job_id so the
    # webapp's /reports/<job_id> view can serve it. Best-effort:
    # a report-write failure SHALL NOT mark the job as failed
    # because the backtest itself succeeded — operators can re-run
    # the CLI path if the bundle is needed.
    report_dir = Path("var") / "reports" / spec.job_id
    report_status = "skipped"
    if not report_dir.exists() or not any(report_dir.iterdir()):
        report_dir.mkdir(parents=True, exist_ok=True)
        write_result = write_report(
            outcome.result,
            config_hash=outcome.config_hash,
            out_dir=report_dir,
            seed=outcome.seed,
            start_at=spec.start,
            end_at=spec.end,
            data_provider=outcome.data_provider,
        )
        report_status = (
            "ok" if not isinstance(write_result, Err) else f"err:{write_result.error.category}"
        )
    else:
        report_status = "exists"

    return {
        "trades_count": str(len(outcome.result.trades)),
        "final_equity_after_tax": str(
            outcome.result.final_equity_after_tax.amount
        ),
        "config_hash": outcome.config_hash,
        "seed": str(outcome.seed),
        "data_provider": outcome.data_provider,
        "equity_curve_points": str(len(outcome.result.equity_curve)),
        "report_dir": str(report_dir),
        "report_status": report_status,
    }


@dataclass(slots=True)
class InProcessJobQueue:
    """ProcessPoolExecutor-backed JobQueue. Phase B v1 — in-memory
    state, no persistence join.

    The worker callable is injectable so tests can swap a synchronous
    stub; the default is ``_default_worker`` which invokes
    ``trading_system.main.run`` in a child process.
    """

    workers: int = 2
    worker_fn: Callable[[JobSpec], dict[str, str]] = _default_worker
    _executor: ProcessPoolExecutor | None = field(default=None, init=False)
    _states: dict[str, JobState] = field(default_factory=dict, init=False)
    _notify: asyncio.Event = field(default=None, init=False)  # type: ignore[assignment]
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        # Lazy executor creation — see _ensure_executor; needed so
        # constructing the queue at app-startup time (outside a
        # running loop) doesn't trip an asyncio import error in the
        # constructor.
        pass

    def _ensure_executor(self) -> ProcessPoolExecutor:
        if self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=self.workers)
        return self._executor

    def _ensure_event(self) -> asyncio.Event:
        loop = asyncio.get_running_loop()
        if self._notify is None or self._loop is not loop:
            self._notify = asyncio.Event()
            self._loop = loop
        return self._notify

    async def submit(self, spec: JobSpec) -> Result[str, str]:
        if spec.job_id in self._states:
            return Err(f"job:duplicate_id:{spec.job_id}")
        # REQ_SDD_FAS_005 — persist PENDING BEFORE executor accepts so
        # a worker crash leaves a resumable row.
        self._states[spec.job_id] = JobState(
            job_id=spec.job_id,
            status=JobStatus.PENDING,
            submitted_at=datetime.now(UTC),
        )
        executor = self._ensure_executor()
        event = self._ensure_event()

        def _on_done(fut: Any) -> None:
            # Called in the executor's callback thread. CPython dict
            # assignment is atomic so the state update lands directly
            # without scheduling on the event loop (which may have
            # already closed in TestClient-style per-request loops).
            # The notify Event is loop-bound — try to set it via
            # call_soon_threadsafe if the loop is still alive, but
            # don't fail if it has closed.
            self._mark_done(spec.job_id, fut)
            loop = self._loop
            event = self._notify
            if loop is not None and event is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(event.set)
                except RuntimeError:
                    # Loop closed between the check and the call —
                    # harmless because state is already up to date.
                    pass

        # Transition to RUNNING immediately on submit (the worker
        # starts at executor's discretion; from the API caller's
        # perspective the job is in flight).
        self._states[spec.job_id] = replace(
            self._states[spec.job_id],
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        future = executor.submit(self.worker_fn, spec)
        future.add_done_callback(_on_done)
        event.set()
        return Ok(spec.job_id)

    def _mark_done(self, job_id: str, future: Any) -> None:
        prev = self._states.get(job_id)
        if prev is None:
            return
        if future.exception() is not None:
            new = replace(
                prev,
                status=JobStatus.FAILED,
                completed_at=datetime.now(UTC),
                error_category=str(future.exception()),
            )
        else:
            new = replace(
                prev,
                status=JobStatus.COMPLETED,
                completed_at=datetime.now(UTC),
                summary=future.result(),
            )
        self._states[job_id] = new

    def status(self, job_id: str) -> Option[JobState]:
        state = self._states.get(job_id)
        if state is None:
            return Nothing()
        return Some(state)

    def all(self) -> tuple[JobState, ...]:
        return tuple(
            sorted(self._states.values(), key=lambda s: s.submitted_at)
        )

    async def stream(
        self, job_id: str, *, poll_interval: float = 0.25
    ) -> AsyncIterator[JobState]:
        event = self._ensure_event()
        last_status: JobStatus | None = None
        # Bail fast on an unknown job rather than streaming forever.
        if job_id not in self._states:
            return
        while True:
            state = self._states.get(job_id)
            if state is None:
                return
            if state.status != last_status:
                yield state
                last_status = state.status
            if state.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                return
            try:
                await asyncio.wait_for(event.wait(), timeout=poll_interval)
                event.clear()
            except asyncio.TimeoutError:
                pass

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None


def new_job_id() -> str:
    """Fresh uuid4 job id — exported so routes share the same
    generation logic as direct callers."""
    return uuid.uuid4().hex
