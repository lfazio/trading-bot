"""Tests for ``trading_system.persistence.migrations.runner``.

Covers TC_PER_002 (idempotent), TC_PER_003 (SHA-locked rejects
edited migrations), TC_PER_004 (--dry-run is read-only).

REQ refs: REQ_F_PER_004, REQ_NF_PER_001, REQ_SDS_PER_003,
REQ_SDD_PER_004.
"""

from __future__ import annotations

from pathlib import Path

from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.result import Err, Ok


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


def _runner(tmp_path: Path, migrations_subdir: str = "migrations") -> MigrationRunner:
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    migrations_dir = tmp_path / migrations_subdir
    migrations_dir.mkdir(parents=True, exist_ok=True)
    return MigrationRunner(conn=conn, migrations_dir=migrations_dir)


def _write(path: Path, sql: str) -> None:
    path.write_text(sql, encoding="utf-8")


# ---------------------------------------------------------------------------
# TC_PER_002 — idempotent: second run is a no-op
# ---------------------------------------------------------------------------


def test_first_run_applies_all_migrations(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    _write(
        runner.migrations_dir / "0001_init.sql",
        "CREATE TABLE foo (id INTEGER PRIMARY KEY);",
    )
    _write(
        runner.migrations_dir / "0002_more.sql",
        "CREATE TABLE bar (id INTEGER PRIMARY KEY);",
    )
    match runner.run():
        case Ok(applied):
            assert applied == ["0001_init.sql", "0002_more.sql"]
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")
    # Tables exist:
    rows = runner.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = [r["name"] for r in rows]
    assert "foo" in names
    assert "bar" in names


def test_second_run_is_noop(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    _write(
        runner.migrations_dir / "0001_init.sql",
        "CREATE TABLE foo (id INTEGER PRIMARY KEY);",
    )
    runner.run()
    match runner.run():
        case Ok(applied):
            assert applied == []
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")


# ---------------------------------------------------------------------------
# TC_PER_003 — SHA-locked: editing an applied migration is rejected
# ---------------------------------------------------------------------------


def test_sha_mismatch_rejects(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    migration = runner.migrations_dir / "0001_init.sql"
    _write(migration, "CREATE TABLE foo (id INTEGER PRIMARY KEY);")
    runner.run()
    # Edit the migration after it was applied.
    _write(migration, "CREATE TABLE foo (id INTEGER PRIMARY KEY, extra TEXT);")
    match runner.run():
        case Err(reason):
            assert reason == "persistence:migration_sha_mismatch:0001_init.sql"
        case Ok(_):
            raise AssertionError("expected Err on SHA mismatch")


# ---------------------------------------------------------------------------
# TC_PER_004 — --dry-run is read-only
# ---------------------------------------------------------------------------


def test_dry_run_lists_pending_without_applying(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    _write(
        runner.migrations_dir / "0001_init.sql",
        "CREATE TABLE foo (id INTEGER PRIMARY KEY);",
    )
    match runner.run(dry_run=True):
        case Ok(applied):
            assert applied == ["0001_init.sql"]
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")
    # Table does NOT exist; schema_migrations is empty.
    table_rows = runner.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='foo'"
    ).fetchall()
    assert table_rows == []
    # schema_migrations is bootstrapped but has no rows.
    mig_rows = runner.conn.execute("SELECT * FROM schema_migrations").fetchall()
    assert mig_rows == []


def test_lexicographic_order(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    _write(runner.migrations_dir / "0003_three.sql", "CREATE TABLE c (x INTEGER);")
    _write(runner.migrations_dir / "0001_one.sql", "CREATE TABLE a (x INTEGER);")
    _write(runner.migrations_dir / "0002_two.sql", "CREATE TABLE b (x INTEGER);")
    match runner.run():
        case Ok(applied):
            assert applied == ["0001_one.sql", "0002_two.sql", "0003_three.sql"]
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")


def test_missing_dir_returns_err(tmp_path: Path) -> None:
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    runner = MigrationRunner(conn=conn, migrations_dir=tmp_path / "does_not_exist")
    match runner.run():
        case Err(reason):
            assert reason.startswith("persistence:migrations_dir_missing")
        case Ok(_):
            raise AssertionError("expected Err")


def test_bundled_0001_init_applies_cleanly(tmp_path: Path) -> None:
    """Sanity-check the migration shipped with the repo applies
    end-to-end against a fresh DB; tables enumerated in the SQL
    should exist after the run."""
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    runner = MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS)
    match runner.run():
        case Ok(applied):
            assert "0001_init.sql" in applied
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")
    table_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in table_rows}
    for required in (
        "equity_points",
        "strategy_registry",
        "registry_promotions",
        "backtest_results",
        "ks_snapshots",
        "capital_flow_initial",
        "capital_flow_injections",
        "schema_migrations",
    ):
        assert required in names, f"table {required} missing from 0001_init"
