"""Tests for ``trading_system.persistence.repositories.snapshot``.

Covers TC_PER_009 (write / get round-trip; ``SnapshotSink`` Protocol
conformance; the filesystem backend toggle is honored).

REQ refs: REQ_F_PER_008, REQ_NF_AUD_001, REQ_SDD_PER_007.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trading_system.models.identifiers import (
    DEFAULT_ACCOUNT_ID,
    AccountId,
    SnapshotId,
)
from trading_system.models.safety import KillSwitchState
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.snapshot import (
    KillSwitchSnapshotRepository,
)
from trading_system.result import Err, Ok
from trading_system.safety.snapshot import (
    AuditSnapshot,
    FileSnapshotSink,
    SnapshotSink,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


def _migrated_conn(tmp_path: Path) -> Connection:
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run()
    return conn


def _snap(sid: str = "snap-1", severity: str = "DEGRADE") -> AuditSnapshot:
    return AuditSnapshot(
        id=SnapshotId(sid),
        at=datetime(2026, 5, 14, 9, 0, tzinfo=UTC),
        state_from=KillSwitchState.ACTIVE,
        state_to=KillSwitchState.DEGRADED,
        trigger_code="financial:drawdown",
        trigger_message="dd > 15%",
        severity=severity,
        payload={"equity": "9500", "drawdown_pct": "0.17", "positions_open": 3},
    )


# ---------------------------------------------------------------------------
# TC_PER_009 — write / get round-trip
# ---------------------------------------------------------------------------


def test_write_then_get_round_trip(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    snap = _snap()
    assert isinstance(repo.write(snap), Ok)
    loaded = repo.get(snap.id).unwrap()
    assert loaded == snap


def test_get_missing_returns_not_found(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    match repo.get(SnapshotId("ghost")):
        case Err(reason):
            assert reason.startswith("persistence:not_found:ks_snapshots:")
        case Ok(_):
            raise AssertionError("expected Err for missing snapshot")


def test_satisfies_snapshot_sink_protocol(tmp_path: Path) -> None:
    """REQ_SDD_PER_007 — the repository is a drop-in for
    ``FileSnapshotSink``: it conforms to the ``SnapshotSink`` Protocol
    (one ``record(snapshot)`` method)."""
    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    assert isinstance(repo, SnapshotSink)
    # The Protocol method itself works (panics on write failure; this
    # path succeeds so no exception expected).
    repo.record(_snap("snap-2"))
    loaded = repo.get(SnapshotId("snap-2")).unwrap()
    assert loaded.id == SnapshotId("snap-2")


def test_filesystem_backend_still_supported(tmp_path: Path) -> None:
    """REQ_SDD_PER_007 — the migration toggle keeps ``FileSnapshotSink``
    available as an export option. The legacy sink is unchanged: its
    ``record`` method appends a JSON line to the configured path."""
    path = tmp_path / "snapshots.jsonl"
    sink = FileSnapshotSink(path=path)
    assert isinstance(sink, SnapshotSink)
    sink.record(_snap("snap-fs"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "snap-fs" in lines[0]


def test_account_isolation_on_snapshots(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    default_repo = KillSwitchSnapshotRepository(
        conn=conn, account_id=DEFAULT_ACCOUNT_ID
    )
    alt_repo = KillSwitchSnapshotRepository(
        conn=conn, account_id=AccountId("alt")
    )
    default_repo.write(_snap("shared"))
    # Alt account should not see the default's snapshot.
    match alt_repo.get(SnapshotId("shared")):
        case Err(reason):
            assert reason.startswith("persistence:not_found:")
        case Ok(_):
            raise AssertionError("alt account leaked default's snapshot")


# ---------------------------------------------------------------------------
# Phase-8 C1 — Err-branch coverage (DB exception paths)
# ---------------------------------------------------------------------------


class _RaisingExecProxy:
    """Delegating proxy around ``sqlite3.Connection`` that raises a
    chosen exception whenever ``execute(sql, ...)`` matches the
    predicate. Used to exercise the repository's DatabaseError
    branches without needing a real corrupt DB."""

    def __init__(self, real, when, exc):
        self._real = real
        self._when = when
        self._exc = exc

    def execute(self, sql, *args, **kwargs):
        if self._when(sql):
            raise self._exc
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        # Delegate everything else (close, commit, etc.) to the real
        # connection. ``__getattr__`` is only consulted for missing
        # attrs, so ``execute`` is intercepted above.
        return getattr(self._real, name)


def _install_raw_execute_interceptor(conn, monkeypatch, *, when, exc):
    """Replace ``conn._raw`` with a proxy that raises ``exc`` on
    matching executes. ``Connection._raw`` is a slot field (settable
    on instances) so ``monkeypatch.setattr`` works against it."""
    proxy = _RaisingExecProxy(conn._raw, when, exc)
    monkeypatch.setattr(conn, "_raw", proxy)


def test_write_integrity_error_surfaces_categorised_err(
    tmp_path: Path, monkeypatch
) -> None:
    """An IntegrityError on insert SHALL surface as
    `persistence:integrity:ks_snapshots:<reason>` and SHALL trigger
    a safe rollback so the connection stays usable."""
    from trading_system.persistence.connection import IntegrityError

    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    _install_raw_execute_interceptor(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO ks_snapshots" in sql,
        exc=IntegrityError("UNIQUE constraint failed"),
    )
    match repo.write(_snap("integrity-probe")):
        case Err(reason):
            assert reason.startswith("persistence:integrity:ks_snapshots:")
        case Ok(_):
            raise AssertionError("expected Err on integrity")


def test_write_operational_error_surfaces_locked_category(
    tmp_path: Path, monkeypatch
) -> None:
    """An OperationalError (busy-locked DB, malformed PRAGMA) SHALL
    surface as `persistence:locked:ks_snapshots:<reason>`."""
    from trading_system.persistence.connection import OperationalError

    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    _install_raw_execute_interceptor(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO ks_snapshots" in sql,
        exc=OperationalError("database is locked"),
    )
    match repo.write(_snap("locked-probe")):
        case Err(reason):
            assert reason.startswith("persistence:locked:ks_snapshots:")
        case Ok(_):
            raise AssertionError("expected Err on operational")


def test_write_generic_database_error_surfaces_corrupt_category(
    tmp_path: Path, monkeypatch
) -> None:
    """Any other ``DatabaseError`` (catch-all) SHALL surface as
    `persistence:corrupt:ks_snapshots:<reason>`."""
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    _install_raw_execute_interceptor(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO ks_snapshots" in sql,
        exc=DatabaseError("disk image corrupt"),
    )
    match repo.write(_snap("corrupt-probe")):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:ks_snapshots:")
        case Ok(_):
            raise AssertionError("expected Err on generic DB")


def test_record_panics_on_write_failure(tmp_path: Path, monkeypatch) -> None:
    """REQ_SDD_PER_007 — the ``SnapshotSink`` Protocol's ``record``
    SHALL panic on write failure since a half-written audit is
    worse than a crash (matches FileSnapshotSink's contract)."""
    import pytest

    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    _install_raw_execute_interceptor(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO ks_snapshots" in sql,
        exc=DatabaseError("disk image corrupt"),
    )
    with pytest.raises(RuntimeError, match="KillSwitchSnapshotRepository.record failed"):
        repo.record(_snap("panic-probe"))


def test_get_database_error_on_read_surfaces_categorised_err(
    tmp_path: Path, monkeypatch
) -> None:
    """A SELECT failure SHALL surface as
    `persistence:corrupt:ks_snapshots:read:<reason>` rather than
    bubbling up the raw sqlite3 exception."""
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    _install_raw_execute_interceptor(
        conn,
        monkeypatch,
        when=lambda sql: sql.lstrip().upper().startswith("SELECT"),
        exc=DatabaseError("read failed"),
    )
    match repo.get(SnapshotId("read-probe")):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:ks_snapshots:read:")
        case Ok(_):
            raise AssertionError("expected Err on read failure")


def test_safe_rollback_swallows_database_errors(
    tmp_path: Path, monkeypatch
) -> None:
    """`_safe_rollback` SHALL NOT propagate a DatabaseError raised
    during rollback — the original write Err is what the caller
    needs, not a secondary rollback fault. Exercises the bare
    except branch under `_safe_rollback`."""
    from trading_system.persistence.connection import (
        DatabaseError,
        IntegrityError,
    )

    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)

    real = conn._raw

    def matcher(sql):
        return (
            "INSERT INTO ks_snapshots" in sql
            or sql.lstrip().upper().startswith("ROLLBACK")
        )

    class _DualFault:
        def execute(self, sql, *args, **kwargs):
            if "INSERT INTO ks_snapshots" in sql:
                raise IntegrityError("simulated integrity")
            if sql.lstrip().upper().startswith("ROLLBACK"):
                raise DatabaseError("rollback also failed")
            return real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(conn, "_raw", _DualFault())
    _ = matcher  # silence unused-warning; logic is inlined in _DualFault
    # Should still surface the original Integrity Err, not a
    # secondary rollback exception.
    match repo.write(_snap("rollback-probe")):
        case Err(reason):
            assert reason.startswith("persistence:integrity:ks_snapshots:")
        case Ok(_):
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# C9 — list_in_window timeline query
# ---------------------------------------------------------------------------


def _snap_at(sid: str, at: datetime, severity: str = "DEGRADE") -> AuditSnapshot:
    """A snapshot pinned to a specific `captured_at` time."""
    return AuditSnapshot(
        id=SnapshotId(sid),
        at=at,
        state_from=KillSwitchState.ACTIVE,
        state_to=KillSwitchState.DEGRADED,
        trigger_code="financial:drawdown",
        trigger_message="dd breached threshold",
        severity=severity,
        payload={"equity": "9500"},
    )


def test_list_in_window_empty_returns_empty_tuple(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    result = repo.list_in_window(
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 12, 31, tzinfo=UTC),
    )
    assert isinstance(result, Ok)
    assert result.value == ()


def test_list_in_window_returns_ascending_timeline(tmp_path: Path) -> None:
    """Snapshots within the window SHALL surface in
    captured_at ASC order — the postmortem timeline."""
    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    repo.write(_snap_at("c", datetime(2026, 5, 14, 12, 0, tzinfo=UTC)))
    repo.write(_snap_at("a", datetime(2026, 5, 14, 10, 0, tzinfo=UTC)))
    repo.write(_snap_at("b", datetime(2026, 5, 14, 11, 0, tzinfo=UTC)))
    result = repo.list_in_window(
        since=datetime(2026, 5, 14, tzinfo=UTC),
        until=datetime(2026, 5, 15, tzinfo=UTC),
    )
    assert isinstance(result, Ok)
    ids = [str(s.id) for s in result.value]
    assert ids == ["a", "b", "c"]


def test_list_in_window_inclusive_bounds(tmp_path: Path) -> None:
    """``since`` + ``until`` are CLOSED bounds — snapshots
    exactly at the edge are included."""
    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    edge = datetime(2026, 5, 14, 10, 0, tzinfo=UTC)
    repo.write(_snap_at("edge", edge))
    result = repo.list_in_window(since=edge, until=edge)
    assert isinstance(result, Ok)
    assert len(result.value) == 1


def test_list_in_window_filters_outside_range(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    repo.write(_snap_at("early", datetime(2026, 1, 1, tzinfo=UTC)))
    repo.write(_snap_at("middle", datetime(2026, 5, 14, tzinfo=UTC)))
    repo.write(_snap_at("late", datetime(2026, 12, 31, tzinfo=UTC)))
    result = repo.list_in_window(
        since=datetime(2026, 5, 1, tzinfo=UTC),
        until=datetime(2026, 5, 31, tzinfo=UTC),
    )
    assert isinstance(result, Ok)
    ids = [str(s.id) for s in result.value]
    assert ids == ["middle"]


def test_list_in_window_open_bounds(tmp_path: Path) -> None:
    """``since=None`` ⇒ no lower bound; ``until=None`` ⇒ no
    upper bound. Both ``None`` ⇒ every snapshot."""
    conn = _migrated_conn(tmp_path)
    repo = KillSwitchSnapshotRepository(conn=conn)
    repo.write(_snap_at("a", datetime(2024, 1, 1, tzinfo=UTC)))
    repo.write(_snap_at("b", datetime(2026, 6, 1, tzinfo=UTC)))
    # No upper bound — both snapshots return.
    full = repo.list_in_window(
        since=datetime(2020, 1, 1, tzinfo=UTC)
    ).unwrap()
    assert {str(s.id) for s in full} == {"a", "b"}
    # No lower bound — both return.
    full = repo.list_in_window(
        until=datetime(2030, 1, 1, tzinfo=UTC)
    ).unwrap()
    assert {str(s.id) for s in full} == {"a", "b"}
    # Both None.
    full = repo.list_in_window().unwrap()
    assert {str(s.id) for s in full} == {"a", "b"}


def test_list_in_window_account_isolation(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    default_repo = KillSwitchSnapshotRepository(conn=conn)
    other = AccountId("alt")
    other_repo = KillSwitchSnapshotRepository(conn=conn, account_id=other)
    default_repo.write(_snap_at("d-1", datetime(2026, 5, 14, tzinfo=UTC)))
    other_repo.write(_snap_at("o-1", datetime(2026, 5, 14, tzinfo=UTC)))
    default_rows = default_repo.list_in_window(
        since=datetime(2026, 1, 1, tzinfo=UTC)
    ).unwrap()
    other_rows = other_repo.list_in_window(
        since=datetime(2026, 1, 1, tzinfo=UTC)
    ).unwrap()
    assert {str(s.id) for s in default_rows} == {"d-1"}
    assert {str(s.id) for s in other_rows} == {"o-1"}
