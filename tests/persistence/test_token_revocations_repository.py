"""CR-024 / TC_TOK_006 + TC_TOK_010 — token-revocation repository.

REQ refs: REQ_F_TOK_002, REQ_NF_TOK_001, REQ_F_PER_002 /003 /004 /009,
REQ_SDS_PER_002, REQ_SDD_PER_004 (SHA-locked migration).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_system.models.identifiers import AccountId
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.token_revocations import (
    OperatorTokenRevocationRepository,
    TokenRevocation,
)
from trading_system.result import Err, Ok


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "state.sqlite"
    connection = Connection.open(db_path).unwrap()
    MigrationRunner(conn=connection, migrations_dir=_BUNDLED_MIGRATIONS).run()
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# TC_TOK_010 — migration audit
# ---------------------------------------------------------------------------


def test_migration_creates_table_and_idempotent(tmp_path: Path) -> None:
    """0007_token_revocations.sql SHALL apply cleanly + the
    bundled MigrationRunner SHALL hold the SHA lock on re-run."""
    db = Connection.open(tmp_path / "state.sqlite").unwrap()
    runner = MigrationRunner(conn=db, migrations_dir=_BUNDLED_MIGRATIONS)
    applied = runner.run().unwrap()
    assert "0007_token_revocations.sql" in applied
    # Schema check.
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='operator_token_revocations'"
    ).fetchall()
    assert len(rows) == 1
    # Column shape (account_id, jti, revoked_at, reason).
    cols = db.execute(
        "PRAGMA table_info(operator_token_revocations)"
    ).fetchall()
    names = {col["name"] for col in cols}
    assert names == {"account_id", "jti", "revoked_at", "reason"}
    # Idempotent re-run.
    again = runner.run().unwrap()
    assert "0007_token_revocations.sql" not in again
    db.close()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_revoke_then_is_revoked_round_trip(conn: Connection) -> None:
    repo = OperatorTokenRevocationRepository(conn=conn)
    result = repo.revoke(
        account_id=AccountId("alpha"),
        jti="deadbeef" * 4,
        reason="leaked-2026-05-26",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    assert isinstance(result, Ok)
    check = repo.is_revoked(
        account_id=AccountId("alpha"), jti="deadbeef" * 4
    )
    assert isinstance(check, Ok) and check.value is True


def test_is_revoked_for_unknown_jti_returns_false(conn: Connection) -> None:
    repo = OperatorTokenRevocationRepository(conn=conn)
    check = repo.is_revoked(account_id=AccountId("alpha"), jti="ghost")
    assert isinstance(check, Ok) and check.value is False


def test_repeat_revoke_is_idempotent(conn: Connection) -> None:
    """Re-revoking the same (account_id, jti) SHALL NOT surface an
    Err — the revocation is already in effect."""
    repo = OperatorTokenRevocationRepository(conn=conn)
    jti = "feedface" * 4
    r1 = repo.revoke(
        account_id=AccountId("alpha"),
        jti=jti,
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    r2 = repo.revoke(
        account_id=AccountId("alpha"),
        jti=jti,
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    assert isinstance(r1, Ok)
    assert isinstance(r2, Ok)


def test_empty_jti_rejected(conn: Connection) -> None:
    repo = OperatorTokenRevocationRepository(conn=conn)
    result = repo.revoke(
        account_id=AccountId("alpha"), jti="", now=None
    )
    assert isinstance(result, Err)
    assert result.error.startswith("persistence:bad_input:")


def test_cross_account_isolation(conn: Connection) -> None:
    repo = OperatorTokenRevocationRepository(conn=conn)
    jti = "cafebabe" * 4
    repo.revoke(
        account_id=AccountId("alpha"),
        jti=jti,
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    check = repo.is_revoked(account_id=AccountId("beta"), jti=jti)
    assert isinstance(check, Ok) and check.value is False


# ---------------------------------------------------------------------------
# TC_TOK_006 — cross-restart durability
# ---------------------------------------------------------------------------


def test_revocation_survives_process_restart(tmp_path: Path) -> None:
    """REQ_F_PER_004 + REQ_NF_PER_001 — the revocation persists
    across a clean shutdown + re-open."""
    db_path = tmp_path / "durable.sqlite"
    conn1 = Connection.open(db_path).unwrap()
    MigrationRunner(conn=conn1, migrations_dir=_BUNDLED_MIGRATIONS).run()
    repo1 = OperatorTokenRevocationRepository(conn=conn1)
    repo1.revoke(
        account_id=AccountId("alpha"),
        jti="durable-jti-test",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    conn1.close()
    # Re-open.
    conn2 = Connection.open(db_path).unwrap()
    repo2 = OperatorTokenRevocationRepository(conn=conn2)
    check = repo2.is_revoked(
        account_id=AccountId("alpha"), jti="durable-jti-test"
    )
    assert isinstance(check, Ok) and check.value is True
    conn2.close()


# ---------------------------------------------------------------------------
# list_all + iteration
# ---------------------------------------------------------------------------


def test_list_all_returns_rows_sorted(conn: Connection) -> None:
    repo = OperatorTokenRevocationRepository(conn=conn)
    repo.revoke(
        account_id=AccountId("gamma"),
        jti="zz",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    repo.revoke(
        account_id=AccountId("alpha"),
        jti="bb",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    repo.revoke(
        account_id=AccountId("alpha"),
        jti="aa",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    rows = repo.list_all().unwrap()
    # Sort: (account_id ASC, jti ASC).
    assert [(str(r.account_id), r.jti) for r in rows] == [
        ("alpha", "aa"),
        ("alpha", "bb"),
        ("gamma", "zz"),
    ]


def test_list_all_scoped_by_account_id(conn: Connection) -> None:
    repo = OperatorTokenRevocationRepository(conn=conn)
    repo.revoke(
        account_id=AccountId("alpha"),
        jti="a-jti",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    repo.revoke(
        account_id=AccountId("beta"),
        jti="b-jti",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    alpha_rows = repo.list_all(account_id=AccountId("alpha")).unwrap()
    assert len(alpha_rows) == 1
    assert alpha_rows[0].jti == "a-jti"


def test_token_revocation_dataclass_rejects_empty_jti() -> None:
    with pytest.raises(ValueError, match="jti must be non-empty"):
        TokenRevocation(
            account_id=AccountId("alpha"),
            jti="",
            revoked_at=datetime(2026, 5, 26, tzinfo=UTC),
        )


# ---------------------------------------------------------------------------
# Multi-process correctness — SQLite WAL propagation
# ---------------------------------------------------------------------------


def test_multi_process_revocation_visible_via_shared_sqlite(
    tmp_path: Path,
) -> None:
    """Two ``OperatorTokenRevocationRepository`` instances backed
    by the same SQLite file SHALL see each other's revocations
    immediately.

    Single-host multi-process deployments (multiple webapp
    workers behind a reverse proxy on the same machine) rely on
    SQLite WAL semantics for cross-process revocation
    propagation: committed writes are visible to readers in
    other connections on their next query, with no
    process-local cache to invalidate. This test pins that
    contract — the v1 design does not need an SSE / DB-NOTIFY
    channel for single-host multi-process scenarios.
    """
    db_path = tmp_path / "shared.sqlite"
    # Two independent connections backing two independent repo
    # instances — same shape as two webapp worker processes
    # opening their own Connection at boot.
    conn_a = Connection.open(db_path).unwrap()
    MigrationRunner(conn=conn_a, migrations_dir=_BUNDLED_MIGRATIONS).run()
    repo_a = OperatorTokenRevocationRepository(conn=conn_a)

    conn_b = Connection.open(db_path).unwrap()
    repo_b = OperatorTokenRevocationRepository(conn=conn_b)
    try:
        # Sanity: neither repo sees the jti yet.
        assert repo_a.is_revoked(
            account_id=AccountId("alpha"), jti="cross-proc-jti"
        ).unwrap() is False
        assert repo_b.is_revoked(
            account_id=AccountId("alpha"), jti="cross-proc-jti"
        ).unwrap() is False

        # Process A revokes.
        repo_a.revoke(
            account_id=AccountId("alpha"),
            jti="cross-proc-jti",
            now=datetime(2026, 5, 26, 12, tzinfo=UTC),
        )

        # Process B's next read SHALL see it (WAL propagates the
        # committed write without explicit invalidation).
        check = repo_b.is_revoked(
            account_id=AccountId("alpha"), jti="cross-proc-jti"
        )
        assert isinstance(check, Ok)
        assert check.value is True, (
            "SQLite WAL did NOT propagate the revocation across "
            "connections — multi-process correctness is broken"
        )

        # Process A's own connection STILL sees the revocation
        # (idempotent read).
        again = repo_a.is_revoked(
            account_id=AccountId("alpha"), jti="cross-proc-jti"
        )
        assert isinstance(again, Ok) and again.value is True
    finally:
        conn_a.close()
        conn_b.close()


def test_multi_process_revoke_idempotent_across_connections(
    tmp_path: Path,
) -> None:
    """Two processes revoking the same ``(account_id, jti)``
    SHALL both succeed — the integrity-error path in
    ``revoke()`` swallows the duplicate-PK collision so neither
    process sees a hard error."""
    db_path = tmp_path / "shared.sqlite"
    conn_a = Connection.open(db_path).unwrap()
    MigrationRunner(conn=conn_a, migrations_dir=_BUNDLED_MIGRATIONS).run()
    repo_a = OperatorTokenRevocationRepository(conn=conn_a)

    conn_b = Connection.open(db_path).unwrap()
    repo_b = OperatorTokenRevocationRepository(conn=conn_b)
    try:
        first = repo_a.revoke(
            account_id=AccountId("alpha"),
            jti="dup-jti",
            now=datetime(2026, 5, 26, 12, tzinfo=UTC),
        )
        second = repo_b.revoke(
            account_id=AccountId("alpha"),
            jti="dup-jti",
            now=datetime(2026, 5, 26, 12, tzinfo=UTC),
        )
        assert isinstance(first, Ok)
        assert isinstance(second, Ok)
        # The revocation lands exactly once — list_all SHALL
        # return one row regardless of which repo did the
        # original insert.
        rows = repo_a.list_all(account_id=AccountId("alpha")).unwrap()
        assert len(rows) == 1
        assert rows[0].jti == "dup-jti"
    finally:
        conn_a.close()
        conn_b.close()
