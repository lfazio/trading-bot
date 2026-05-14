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
