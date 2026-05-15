"""Tests for ``trading_system.persistence.repositories.transition``.

Covers the CR-013 persistence slice:

- TransitionEvent round-trip through ``transition_event_to_row`` /
  ``row_to_transition_event`` (REQ_NF_RGM_001 / REQ_NF_PER_001).
- ``append`` / ``latest`` / ``history`` semantics.
- Cross-account isolation (REQ_F_PER_009 / REQ_SDD_PER_008).
- ``TransitionTracker.from_seed`` rehydration from the repository
  (TC_RGM_010 / REQ_SDD_RGM_005).
- Bundled 0002_regime.sql migration applies cleanly.

REQ refs: REQ_F_PER_002, REQ_F_PER_003, REQ_F_PER_009, REQ_NF_RGM_001,
REQ_SDD_RGM_005, REQ_SDS_PER_002, REQ_SDD_PER_002.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_system.models.identifiers import (
    DEFAULT_ACCOUNT_ID,
    AccountId,
    SnapshotId,
)
from trading_system.models.phase import MarketRegime
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.transition import TransitionRepository
from trading_system.regime.transition import TransitionEvent, TransitionTracker
from trading_system.result import Err, Nothing, Ok, Some

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


def _migrated_conn(tmp_path: Path) -> Connection:
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run()
    return conn


def _event(day: int, *, frm: MarketRegime, to: MarketRegime) -> TransitionEvent:
    return TransitionEvent(
        from_regime=frm,
        to_regime=to,
        at=datetime(2026, 5, day, 9, 0, tzinfo=UTC),
        confirmation_periods=2,
    )


# ---------------------------------------------------------------------------
# Bundled migration applies cleanly
# ---------------------------------------------------------------------------


def test_0002_regime_migration_creates_transitions_table(tmp_path: Path) -> None:
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    runner = MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS)
    applied = runner.run().unwrap()
    assert "0001_init.sql" in applied
    assert "0002_regime.sql" in applied
    # Schema check — `transitions` exists.
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='transitions'"
    ).fetchall()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Round-trip + append/latest
# ---------------------------------------------------------------------------


def test_append_then_latest_round_trips(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = TransitionRepository(conn=conn)
    event = _event(8, frm=MarketRegime.BULL, to=MarketRegime.BEAR)
    res = repo.append(event, snapshot_id=SnapshotId("snap-1"))
    assert isinstance(res, Ok)
    loaded = repo.latest().unwrap()
    match loaded:
        case Some(e):
            assert e == event
        case _:
            raise AssertionError("expected Some(event)")


def test_latest_empty_returns_nothing(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = TransitionRepository(conn=conn)
    match repo.latest():
        case Ok(Nothing()):
            pass
        case other:
            raise AssertionError(f"expected Ok(Nothing()), got {other!r}")


def test_append_rejects_empty_snapshot_id(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = TransitionRepository(conn=conn)
    event = _event(8, frm=MarketRegime.BULL, to=MarketRegime.BEAR)
    res = repo.append(event, snapshot_id=SnapshotId(""))
    match res:
        case Err(reason):
            assert reason == "persistence:integrity:transitions:empty_snapshot_id"
        case Ok(_):
            raise AssertionError("expected Err for empty snapshot_id")


def test_duplicate_at_returns_integrity_err(tmp_path: Path) -> None:
    """REQ_F_PER_003 — second insert at the same (account_id, at)
    violates the PK and rolls back; row count unchanged."""
    conn = _migrated_conn(tmp_path)
    repo = TransitionRepository(conn=conn)
    event = _event(8, frm=MarketRegime.BULL, to=MarketRegime.BEAR)
    repo.append(event, snapshot_id=SnapshotId("snap-1"))
    before = conn.execute("SELECT COUNT(*) AS n FROM transitions").fetchone()["n"]
    res = repo.append(event, snapshot_id=SnapshotId("snap-1-dup"))
    match res:
        case Err(reason):
            assert reason.startswith("persistence:integrity:transitions")
        case Ok(_):
            raise AssertionError("expected Err on duplicate (account_id, at)")
    after = conn.execute("SELECT COUNT(*) AS n FROM transitions").fetchone()["n"]
    assert before == after, "row count must be unchanged after a failed insert"


# ---------------------------------------------------------------------------
# history() — chronological ordering
# ---------------------------------------------------------------------------


def test_history_returns_events_in_at_order(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = TransitionRepository(conn=conn)
    # Insert out of order.
    repo.append(_event(10, frm=MarketRegime.BEAR, to=MarketRegime.HIGH_VOL),
                snapshot_id=SnapshotId("s10"))
    repo.append(_event(8, frm=MarketRegime.BULL, to=MarketRegime.BEAR),
                snapshot_id=SnapshotId("s8"))
    repo.append(_event(9, frm=MarketRegime.BEAR, to=MarketRegime.SIDEWAYS),
                snapshot_id=SnapshotId("s9"))
    events = repo.history().unwrap()
    days = [e.at.day for e in events]
    assert days == [8, 9, 10]


def test_latest_returns_most_recent_event(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = TransitionRepository(conn=conn)
    repo.append(_event(8, frm=MarketRegime.BULL, to=MarketRegime.BEAR),
                snapshot_id=SnapshotId("s8"))
    repo.append(_event(10, frm=MarketRegime.BEAR, to=MarketRegime.HIGH_VOL),
                snapshot_id=SnapshotId("s10"))
    repo.append(_event(9, frm=MarketRegime.BEAR, to=MarketRegime.SIDEWAYS),
                snapshot_id=SnapshotId("s9"))
    match repo.latest():
        case Ok(Some(e)):
            assert e.at.day == 10
            assert e.to_regime is MarketRegime.HIGH_VOL
        case _:
            raise AssertionError("expected the most recent transition")


# ---------------------------------------------------------------------------
# Account isolation (REQ_F_PER_009 / REQ_SDD_PER_008)
# ---------------------------------------------------------------------------


def test_cross_account_isolation(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = TransitionRepository(conn=conn)
    default_event = _event(8, frm=MarketRegime.BULL, to=MarketRegime.BEAR)
    alt = AccountId("alt")
    alt_event = _event(8, frm=MarketRegime.BEAR, to=MarketRegime.HIGH_VOL)
    repo.append(default_event, snapshot_id=SnapshotId("s-default"),
                account_id=DEFAULT_ACCOUNT_ID)
    repo.append(alt_event, snapshot_id=SnapshotId("s-alt"), account_id=alt)
    default_latest = repo.latest(account_id=DEFAULT_ACCOUNT_ID).unwrap()
    alt_latest = repo.latest(account_id=alt).unwrap()
    match default_latest, alt_latest:
        case Some(d), Some(a):
            assert d == default_event
            assert a == alt_event
        case _:
            raise AssertionError("both accounts should hold their own row")
    # Reading a non-existent account_id returns Nothing, not the default's row.
    ghost = repo.latest(account_id=AccountId("ghost")).unwrap()
    assert isinstance(ghost, Nothing)


# ---------------------------------------------------------------------------
# TC_RGM_010 — restart rehydration via TransitionTracker.from_seed
# ---------------------------------------------------------------------------


def test_from_seed_rehydrates_from_repository_latest(tmp_path: Path) -> None:
    """REQ_SDD_RGM_005 — on startup, the operator reads
    repo.latest(account_id) and seeds the tracker with the
    ``to_regime`` of the most recent persisted transition."""
    conn = _migrated_conn(tmp_path)
    repo = TransitionRepository(conn=conn)
    repo.append(
        _event(8, frm=MarketRegime.BULL, to=MarketRegime.BEAR),
        snapshot_id=SnapshotId("s-pre-restart"),
    )

    # Restart: read latest, seed a fresh tracker with its to_regime.
    latest = repo.latest().unwrap()
    match latest:
        case Some(prev):
            tracker = TransitionTracker.from_seed(
                confirmation_periods=2,
                current=prev.to_regime,
            )
        case Nothing():
            raise AssertionError("seed event must exist")

    # The tracker's cursor SHALL be at BEAR (the persisted to_regime).
    match tracker.current_regime:
        case Some(r):
            assert r is MarketRegime.BEAR
        case _:
            raise AssertionError("tracker cursor not rehydrated")

    # A subsequent BULL → emit only after the confirmation window.
    after = datetime(2026, 5, 8, 9, 0, tzinfo=UTC)
    assert isinstance(
        tracker.observe(MarketRegime.BULL, at=after + timedelta(hours=1)),
        Nothing,
    )
    event2 = tracker.observe(MarketRegime.BULL, at=after + timedelta(hours=2))
    match event2:
        case Some(e):
            # from_regime is the rehydrated cursor — BEAR.
            assert e.from_regime is MarketRegime.BEAR
            assert e.to_regime is MarketRegime.BULL
        case _:
            raise AssertionError("expected BEAR → BULL after seed + window")


def test_persistence_round_trip_with_subsequent_append(tmp_path: Path) -> None:
    """Confirm that after rehydrating + emitting a new transition, the
    second transition persists and `latest()` advances."""
    conn = _migrated_conn(tmp_path)
    repo = TransitionRepository(conn=conn)
    repo.append(
        _event(8, frm=MarketRegime.BULL, to=MarketRegime.BEAR),
        snapshot_id=SnapshotId("s-1"),
    )
    new_event = _event(15, frm=MarketRegime.BEAR, to=MarketRegime.BULL)
    repo.append(new_event, snapshot_id=SnapshotId("s-2"))
    history = repo.history().unwrap()
    assert len(history) == 2
    assert history[-1] == new_event
    latest = repo.latest().unwrap()
    match latest:
        case Some(e):
            assert e == new_event
        case _:
            raise AssertionError
