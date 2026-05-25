"""Tests for ``trading_system.models.jobs``.

Phase-8 C1 coverage cleanup. Pins the invariant validators on
``BacktestJobSpec`` + ``BacktestJobState`` so a config typo /
mis-wired worker SHALL surface at construction instead of
silently mis-serialising downstream.

REQ refs: REQ_F_WEB_003, REQ_F_WEB_009, REQ_SDD_WEB_005,
REQ_SDS_WEB_003, REQ_NF_WEB_002 (canonical-JSON shape pin).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_system.models.jobs import (
    BacktestJobSpec,
    BacktestJobState,
    JobStatus,
)


_START = datetime(2026, 1, 1, tzinfo=UTC)
_END = datetime(2026, 4, 1, tzinfo=UTC)
_SUBMITTED = datetime(2026, 5, 23, 12, tzinfo=UTC)


def _ok_spec_kwargs() -> dict:
    return {
        "job_id": "job-001",
        "config_dir": "config",
        "start": _START,
        "end": _END,
    }


# ---------------------------------------------------------------------------
# BacktestJobSpec invariants
# ---------------------------------------------------------------------------


class TestBacktestJobSpec:
    def test_minimal_spec_constructs(self) -> None:
        spec = BacktestJobSpec(**_ok_spec_kwargs())
        assert spec.job_id == "job-001"
        assert spec.config_dir == "config"
        assert spec.with_slippage is False
        assert spec.account_id == "default"

    def test_with_slippage_default_is_false(self) -> None:
        spec = BacktestJobSpec(**_ok_spec_kwargs())
        assert spec.with_slippage is False

    def test_account_id_default_is_default(self) -> None:
        """REQ_NF_ACC_001 — single-account legacy default."""
        spec = BacktestJobSpec(**_ok_spec_kwargs())
        assert spec.account_id == "default"

    def test_empty_job_id_rejected(self) -> None:
        kwargs = _ok_spec_kwargs()
        kwargs["job_id"] = ""
        with pytest.raises(ValueError, match="job_id must be non-empty"):
            BacktestJobSpec(**kwargs)

    def test_whitespace_only_job_id_rejected(self) -> None:
        kwargs = _ok_spec_kwargs()
        kwargs["job_id"] = "   "
        with pytest.raises(ValueError, match="job_id must be non-empty"):
            BacktestJobSpec(**kwargs)

    def test_empty_config_dir_rejected(self) -> None:
        kwargs = _ok_spec_kwargs()
        kwargs["config_dir"] = ""
        with pytest.raises(ValueError, match="config_dir must be non-empty"):
            BacktestJobSpec(**kwargs)

    def test_empty_account_id_rejected(self) -> None:
        kwargs = _ok_spec_kwargs()
        kwargs["account_id"] = ""
        with pytest.raises(ValueError, match="account_id must be non-empty"):
            BacktestJobSpec(**kwargs)

    def test_end_before_start_rejected(self) -> None:
        kwargs = _ok_spec_kwargs()
        kwargs["start"] = _END
        kwargs["end"] = _START
        with pytest.raises(ValueError, match="end must be >= start"):
            BacktestJobSpec(**kwargs)

    def test_end_equals_start_accepted(self) -> None:
        """Inclusive boundary — zero-length window is a legitimate
        smoke-test backtest (no ticks consumed)."""
        kwargs = _ok_spec_kwargs()
        kwargs["end"] = kwargs["start"]
        spec = BacktestJobSpec(**kwargs)
        assert spec.start == spec.end

    def test_spec_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        spec = BacktestJobSpec(**_ok_spec_kwargs())
        with pytest.raises(FrozenInstanceError):
            spec.job_id = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BacktestJobState invariants
# ---------------------------------------------------------------------------


def _ok_state_kwargs() -> dict:
    return {
        "job_id": "job-001",
        "status": JobStatus.PENDING,
        "submitted_at": _SUBMITTED,
    }


class TestBacktestJobState:
    def test_minimal_pending_state_constructs(self) -> None:
        state = BacktestJobState(**_ok_state_kwargs())
        assert state.status is JobStatus.PENDING
        assert state.started_at is None
        assert state.completed_at is None
        assert state.error_category is None
        assert state.summary == {}

    def test_empty_job_id_rejected(self) -> None:
        kwargs = _ok_state_kwargs()
        kwargs["job_id"] = ""
        with pytest.raises(ValueError, match="job_id must be non-empty"):
            BacktestJobState(**kwargs)

    def test_failed_status_requires_error_category(self) -> None:
        """REQ_SDD_WEB_005 — a FAILED state SHALL carry a non-empty
        ``error_category`` so the reports view can surface a clear
        failure reason without inspecting the worker log."""
        kwargs = _ok_state_kwargs()
        kwargs["status"] = JobStatus.FAILED
        with pytest.raises(ValueError, match="error_category"):
            BacktestJobState(**kwargs)

    def test_failed_with_error_category_accepted(self) -> None:
        kwargs = _ok_state_kwargs()
        kwargs["status"] = JobStatus.FAILED
        kwargs["error_category"] = "worker:crash"
        state = BacktestJobState(**kwargs)
        assert state.status is JobStatus.FAILED
        assert state.error_category == "worker:crash"

    def test_state_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        state = BacktestJobState(**_ok_state_kwargs())
        with pytest.raises(FrozenInstanceError):
            state.status = JobStatus.RUNNING  # type: ignore[misc]


# ---------------------------------------------------------------------------
# JobStatus enum
# ---------------------------------------------------------------------------


def test_job_status_values() -> None:
    """StrEnum values are the canonical strings the persistence layer
    + canonical-JSON serialiser round-trip; lock them down."""
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.RUNNING.value == "running"
    assert JobStatus.COMPLETED.value == "completed"
    assert JobStatus.FAILED.value == "failed"
