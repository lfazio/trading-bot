"""Async-backtest job models — CR-004 Phase B
(REQ_F_WEB_003 / REQ_F_WEB_009 / REQ_SDD_WEB_005 / REQ_SDS_WEB_003).

These data carriers are shared between the webui ``JobQueue``
Protocol (defines submit / status / list_for_account) and every
concrete backend (in-process worker, SQLite-backed repository,
operator-injected stub). They live in ``models/`` so neither side
imports the other.

Schema invariants:
- ``BacktestJobSpec`` is immutable — once submitted, the spec is
  the immutable record of intent. Workers don't rewrite it.
- ``BacktestJobState`` is also immutable — workers persist a new
  state row per transition; the "current" state is the latest row.
- ``summary`` is a flat ``dict[str, str]`` so it serialises via
  canonical-JSON byte-identically (no nested objects, no Decimals
  mid-payload — Decimals serialise via ``str()`` at write time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class JobStatus(StrEnum):
    """Lifecycle states for a backtest job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BacktestJobSpec:
    """Immutable description of one backtest job.

    Fields:
        job_id        — caller-generated identifier (the route uses
                        ``uuid.uuid4().hex``; tests inject a
                        deterministic id).
        config_dir    — path to the YAML config bundle the backtest
                        driver should consume. Validated at submit
                        time by the backend; the repo SHALL NOT
                        materialise the directory itself.
        start / end   — backtest window (datetime, timezone aware
                        recommended; the repo serialises via
                        ``isoformat()``).
        with_slippage — whether the slippage model is applied.
        account_id    — owner of the job; the repository keys rows
                        on ``(account_id, job_id)``.
    """

    job_id: str
    config_dir: str
    start: datetime
    end: datetime
    with_slippage: bool = False
    account_id: str = "default"

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("BacktestJobSpec.job_id must be non-empty")
        if not self.config_dir.strip():
            raise ValueError("BacktestJobSpec.config_dir must be non-empty")
        if not self.account_id.strip():
            raise ValueError("BacktestJobSpec.account_id must be non-empty")
        if self.end < self.start:
            raise ValueError(
                f"BacktestJobSpec.end must be >= start, "
                f"got start={self.start.isoformat()}, end={self.end.isoformat()}"
            )


@dataclass(frozen=True, slots=True)
class BacktestJobState:
    """Snapshot of one job's current state.

    Returned by ``JobQueue.status`` (always the latest transition
    for a given job) and ``JobQueue.list_for_account``.

    Fields beyond ``status`` carry the lifecycle timestamps + the
    error category / summary the worker recorded. ``summary`` is a
    flat ``dict[str, str]`` — the JobQueue Protocol contract is
    explicit about the shape so canonical-JSON serialisation stays
    byte-identical on replay (REQ_NF_WEB_002).
    """

    job_id: str
    status: JobStatus
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_category: str | None = None
    summary: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("BacktestJobState.job_id must be non-empty")
        if self.status == JobStatus.FAILED and not self.error_category:
            raise ValueError(
                "BacktestJobState.error_category must be set when status=FAILED"
            )
