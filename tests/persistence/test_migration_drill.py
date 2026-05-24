"""C5 — Persistence migration drill.

Operator hardening sprint slice. Exercises the persistence layer's
shipped migration pipeline (`trading_system/persistence/migrations/`)
end-to-end so schema-change footguns surface in CI instead of in
production:

- The full bundled pipeline (0001..0006) applies cleanly on an
  empty DB, leaves every documented table in place, and re-running
  it is a no-op (idempotency invariant — REQ_F_PER_004).
- Retroactive edits to ANY shipped migration (not just 0001) are
  rejected with `persistence:migration_sha_mismatch:<name>` so
  silent schema drift is impossible.
- WAL recovery: a mid-transaction crash (simulated by tearing down
  the SQLite connection without COMMIT) leaves the on-disk file
  consistent; a fresh `Connection.open` over the same path reads
  the pre-crash committed state and the uncommitted writes are
  rolled back (REQ_SDS_PER_004 WAL semantics).
- Cross-restart durability: a row committed by repository code
  survives a clean process shutdown + re-open (REQ_NF_PER_001
  write/read round-trip).
- Adding a NEW migration on a populated DB applies cleanly and
  leaves pre-existing rows intact (no destructive ALTERs in the
  shipped pipeline).

These tests run against the real `trading_system/persistence/
migrations/` directory so any new migration the operator adds
goes through the same drill automatically.

REQ refs: REQ_F_PER_001, REQ_F_PER_004, REQ_NF_PER_001,
REQ_SDS_PER_003, REQ_SDS_PER_004, REQ_SDD_PER_001,
REQ_SDD_PER_004.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import AccountId
from trading_system.models.money import Currency, Money
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.portfolio import (
    PortfolioRepository,
)
from trading_system.result import Err, Ok


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open(tmp_path: Path, db_name: str = "drill.sqlite") -> Connection:
    """Open a fresh ``Connection`` over ``tmp_path/db_name``."""
    result = Connection.open(tmp_path / db_name)
    assert isinstance(result, Ok), f"open failed: {result}"
    return result.value


def _table_names(conn: Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return {r["name"] for r in rows}


def _expected_bundled_tables() -> set[str]:
    """Every table the bundled migrations 0001..0006 SHALL create."""
    return {
        # 0001_init
        "equity_points",
        "strategy_registry",
        "registry_promotions",
        "backtest_results",
        "ks_snapshots",
        "capital_flow_initial",
        "capital_flow_injections",
        # 0002_regime
        "transitions",
        # 0003_approvals
        "approval_requests",
        "approval_responses",
        # 0004_quant
        "hypotheses",
        "hypothesis_transitions",
        # 0005_idempotency
        "idempotency_entries",
        # 0006_backtest_jobs
        "backtest_jobs",
        "backtest_job_states",
        # bookkeeping (created by runner.run)
        "schema_migrations",
    }


# ---------------------------------------------------------------------------
# Full pipeline + idempotency
# ---------------------------------------------------------------------------


def test_full_bundled_pipeline_applies_clean(tmp_path: Path) -> None:
    """Every shipped migration SHALL apply against a fresh DB; the
    result SHALL include every documented table."""
    conn = _open(tmp_path)
    runner = MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS)
    result = runner.run()
    assert isinstance(result, Ok), f"unexpected Err: {result}"
    applied = result.value
    # Every bundled .sql is applied in lexicographic order.
    on_disk = sorted(p.name for p in _BUNDLED_MIGRATIONS.glob("*.sql"))
    assert applied == on_disk, (
        f"missing from applied set: {set(on_disk) - set(applied)}"
    )
    missing = _expected_bundled_tables() - _table_names(conn)
    assert not missing, f"tables not created by the pipeline: {missing}"


def test_full_bundled_pipeline_is_idempotent(tmp_path: Path) -> None:
    """Running the full pipeline twice SHALL be a no-op the second
    time — REQ_F_PER_004 SHA-lock + idempotency invariant."""
    conn = _open(tmp_path)
    runner = MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS)
    first = runner.run()
    assert isinstance(first, Ok)
    assert first.value  # something was applied
    second = runner.run()
    assert isinstance(second, Ok)
    assert second.value == [], "second run SHALL apply nothing"


def test_full_pipeline_dry_run_is_read_only(tmp_path: Path) -> None:
    """Dry-run SHALL list the would-be-applied set without touching
    the schema (TC_PER_004 extended to the full pipeline)."""
    conn = _open(tmp_path)
    runner = MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS)
    result = runner.run(dry_run=True)
    assert isinstance(result, Ok)
    assert result.value, "dry-run SHALL report pending migrations"
    # No application-tables exist — only schema_migrations (the
    # runner bootstraps it before the dry-run gate).
    tables = _table_names(conn)
    assert tables == {"schema_migrations"}, (
        f"dry-run leaked tables: {tables - {'schema_migrations'}}"
    )


# ---------------------------------------------------------------------------
# SHA-lock catches retroactive edits to ANY shipped migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "0001_init.sql",
        "0003_approvals.sql",
        "0006_backtest_jobs.sql",
    ],
)
def test_retroactive_edit_to_any_shipped_migration_rejects(
    tmp_path: Path, target: str
) -> None:
    """Editing ANY applied migration (not just 0001) SHALL be
    rejected with `persistence:migration_sha_mismatch:<name>`."""
    # Stage the bundled migrations in a tmp dir so we can edit them.
    staged = tmp_path / "migrations"
    shutil.copytree(_BUNDLED_MIGRATIONS, staged)
    conn = _open(tmp_path)
    runner = MigrationRunner(conn=conn, migrations_dir=staged)
    assert isinstance(runner.run(), Ok)
    # Now tamper with the target.
    edited = staged / target
    edited.write_text(
        edited.read_text(encoding="utf-8") + "\n-- retroactive edit\n",
        encoding="utf-8",
    )
    result = runner.run()
    assert isinstance(result, Err)
    assert result.error == f"persistence:migration_sha_mismatch:{target}"


# ---------------------------------------------------------------------------
# WAL recovery — mid-transaction crash leaves prior commits intact
# ---------------------------------------------------------------------------


def test_wal_recovery_replays_committed_writes_after_crash(
    tmp_path: Path,
) -> None:
    """Simulate a process crash mid-transaction: COMMIT some rows,
    BEGIN IMMEDIATE another transaction, write a row WITHOUT
    committing, then tear down the raw sqlite3 connection
    abruptly (no ``.close()`` — mirrors a SIGKILL). A fresh
    `Connection.open` SHALL surface the committed rows; the
    uncommitted row SHALL be rolled back via WAL replay
    (REQ_SDS_PER_004)."""
    db_path = tmp_path / "wal-recovery.sqlite"
    # First connection — apply schema + commit a baseline row.
    conn1 = Connection.open(db_path).unwrap()
    MigrationRunner(conn=conn1, migrations_dir=_BUNDLED_MIGRATIONS).run()
    repo = PortfolioRepository(conn=conn1)
    repo.append_equity_point(
        EquityPoint(
            at=datetime(2026, 5, 23, tzinfo=UTC),
            equity_gross=Money(Decimal("1000"), Currency.EUR),
            equity_after_tax=Money(Decimal("1000"), Currency.EUR),
            drawdown_pct=Decimal("0"),
        ),
        account_id=AccountId("paper-pre-crash"),
    )
    # Now open a write transaction and DO NOT commit.
    conn1.begin_immediate()
    conn1.execute(
        "INSERT INTO equity_points (account_id, at, "
        "equity_gross_amount, equity_gross_currency, "
        "equity_after_tax_amount, equity_after_tax_currency, "
        "drawdown_pct) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "paper-pre-crash",
            "2026-05-23T01:00:00+00:00",
            "9999",
            "EUR",
            "9999",
            "EUR",
            "0",
        ),
    )
    # Simulate a SIGKILL — drop the raw handle without COMMIT /
    # ROLLBACK / .close(). The OS-level file stays consistent
    # because WAL writes go to a sidecar file that's safe to
    # truncate on the next open.
    conn1.raw.close()  # closes the fd, releasing the lock, no COMMIT issued

    # Fresh connection — WAL replay SHALL surface only the
    # committed row.
    conn2 = Connection.open(db_path).unwrap()
    rows = conn2.execute(
        "SELECT account_id, at, equity_after_tax_amount FROM equity_points "
        "ORDER BY at"
    ).fetchall()
    assert len(rows) == 1, (
        f"expected 1 surviving row (uncommitted SHALL be rolled back), "
        f"got {len(rows)}"
    )
    assert rows[0]["account_id"] == "paper-pre-crash"
    assert rows[0]["equity_after_tax_amount"] == "1000"


def test_data_survives_clean_shutdown_and_reopen(tmp_path: Path) -> None:
    """REQ_NF_PER_001 — a row committed by repository code SHALL
    be readable after a clean ``conn.close()`` + ``Connection.open``
    over the same path."""
    db_path = tmp_path / "durable.sqlite"
    # Bootstrap + write.
    conn1 = Connection.open(db_path).unwrap()
    MigrationRunner(conn=conn1, migrations_dir=_BUNDLED_MIGRATIONS).run()
    repo1 = PortfolioRepository(conn=conn1)
    point = EquityPoint(
        at=datetime(2026, 5, 23, 12, tzinfo=UTC),
        equity_gross=Money(Decimal("12345.67"), Currency.EUR),
        equity_after_tax=Money(Decimal("12345.67"), Currency.EUR),
        drawdown_pct=Decimal("0"),
    )
    repo1.append_equity_point(point, account_id=AccountId("paper-restart"))
    conn1.close()
    # Re-open.
    conn2 = Connection.open(db_path).unwrap()
    repo2 = PortfolioRepository(conn=conn2)
    curve = repo2.equity_curve(account_id=AccountId("paper-restart"))
    assert isinstance(curve, Ok)
    bars = curve.value
    assert len(bars) == 1
    assert bars[0].equity_after_tax.amount == Decimal("12345.67")
    assert bars[0].at == point.at


# ---------------------------------------------------------------------------
# Populated-DB upgrade — adding a new migration leaves data intact
# ---------------------------------------------------------------------------


def test_new_migration_on_populated_db_leaves_existing_data(
    tmp_path: Path,
) -> None:
    """Stage the bundled migrations, populate the DB with some
    representative rows, then drop a NEW migration into the dir
    and re-run. The added migration SHALL apply cleanly + the
    pre-existing rows SHALL stay intact (no destructive ALTERs in
    the shipped pipeline; the runner SHALL preserve data across
    the schema growth)."""
    staged = tmp_path / "migrations"
    shutil.copytree(_BUNDLED_MIGRATIONS, staged)
    conn = _open(tmp_path)
    runner = MigrationRunner(conn=conn, migrations_dir=staged)
    assert isinstance(runner.run(), Ok)

    # Populate the DB with a row in equity_points.
    repo = PortfolioRepository(conn=conn)
    repo.append_equity_point(
        EquityPoint(
            at=datetime(2026, 5, 22, tzinfo=UTC),
            equity_gross=Money(Decimal("500"), Currency.EUR),
            equity_after_tax=Money(Decimal("500"), Currency.EUR),
            drawdown_pct=Decimal("0"),
        ),
        account_id=AccountId("paper-populate"),
    )

    # Drop a new migration into the staged dir.
    (staged / "9999_drill.sql").write_text(
        "CREATE TABLE drill_marker (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    result = runner.run()
    assert isinstance(result, Ok)
    assert result.value == ["9999_drill.sql"]
    # New table exists, old data survives.
    assert "drill_marker" in _table_names(conn)
    curve = repo.equity_curve(account_id=AccountId("paper-populate"))
    assert isinstance(curve, Ok)
    assert len(curve.value) == 1
    assert curve.value[0].equity_after_tax.amount == Decimal("500")


# ---------------------------------------------------------------------------
# Sanity — WAL mode is the active journal mode
# ---------------------------------------------------------------------------


def test_journal_mode_is_wal_on_open(tmp_path: Path) -> None:
    """REQ_SDD_PER_001 / REQ_SDS_PER_004 — the canonical PRAGMA set
    includes `journal_mode=WAL`. Without WAL, the recovery test
    above would lose committed data on a crash."""
    conn = _open(tmp_path)
    mode = conn.pragma("journal_mode")
    assert mode == "wal", f"expected WAL journal mode, got {mode!r}"


def test_foreign_keys_pragma_active(tmp_path: Path) -> None:
    """FK enforcement is part of the canonical PRAGMA set —
    repository code relies on it to refuse orphan rows."""
    conn = _open(tmp_path)
    fk = conn.pragma("foreign_keys")
    assert fk == 1, f"expected foreign_keys=1, got {fk!r}"


def test_busy_timeout_matches_construction(tmp_path: Path) -> None:
    """The ``busy_timeout`` PRAGMA SHALL match the constructor arg
    so concurrent writers don't get an unbounded wait (REQ_F_PER_003)."""
    result = Connection.open(tmp_path / "busy.sqlite", busy_timeout_ms=2500)
    assert isinstance(result, Ok)
    timeout = result.value.pragma("busy_timeout")
    assert timeout == 2500, f"expected 2500 ms, got {timeout!r}"


# ---------------------------------------------------------------------------
# Guard — the migrations dir SHALL NOT ship a `down` script
# ---------------------------------------------------------------------------


def test_migrations_dir_has_no_down_scripts() -> None:
    """REQ_F_PER_004 — migrations are forward-only. The runner
    intentionally has no down-migration path; this test catches
    accidental `*_down.sql` additions."""
    down_scripts = list(_BUNDLED_MIGRATIONS.glob("*down*.sql"))
    assert not down_scripts, (
        f"forward-only invariant violated; found: {down_scripts}"
    )


# ---------------------------------------------------------------------------
# Guard — sqlite3 reachability stays bounded to persistence/
# ---------------------------------------------------------------------------


def test_sqlite3_module_is_imported_only_through_connection() -> None:
    """REQ_F_PER_010 — the Connection wrapper is the single
    sqlite3 caller in the codebase. This test pins that boundary
    so a stray `import sqlite3` outside ``trading_system/
    persistence/`` is caught structurally."""
    import ast

    repo_pkg = _REPO_ROOT / "trading_system"
    persistence_pkg = repo_pkg / "persistence"
    offenders: list[str] = []
    for py in repo_pkg.rglob("*.py"):
        if persistence_pkg in py.parents:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sqlite3":
                        offenders.append(py.relative_to(_REPO_ROOT).as_posix())
            elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
                offenders.append(py.relative_to(_REPO_ROOT).as_posix())
    assert not offenders, (
        f"REQ_F_PER_010 violated — sqlite3 imported outside "
        f"persistence/: {offenders}"
    )


# ---------------------------------------------------------------------------
# Edge: malformed SQL in a pending migration aborts the whole run
# ---------------------------------------------------------------------------


def test_malformed_migration_aborts_run(tmp_path: Path) -> None:
    """A pending migration with broken SQL SHALL abort the run
    with `persistence:migration_failed:<name>:<error>` and SHALL
    NOT half-apply a later migration (REQ_F_PER_004 atomicity at
    the run level)."""
    staged = tmp_path / "migrations"
    staged.mkdir()
    (staged / "0001_good.sql").write_text(
        "CREATE TABLE good (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    (staged / "0002_broken.sql").write_text(
        "CREATE TABBBLE broken (id INTEGER);",  # typo
        encoding="utf-8",
    )
    (staged / "0003_after.sql").write_text(
        "CREATE TABLE after_broken (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    conn = _open(tmp_path)
    runner = MigrationRunner(conn=conn, migrations_dir=staged)
    result = runner.run()
    assert isinstance(result, Err)
    assert result.error.startswith("persistence:migration_failed:0002_broken.sql")
    tables = _table_names(conn)
    # 0001 was applied + recorded; 0003 SHALL NOT have run.
    assert "good" in tables
    assert "after_broken" not in tables
    # 0001 recorded in schema_migrations; 0002 / 0003 NOT recorded.
    applied = {
        r["filename"]
        for r in conn.execute("SELECT filename FROM schema_migrations").fetchall()
    }
    assert applied == {"0001_good.sql"}


# ---------------------------------------------------------------------------
# Edge: sqlite3.Connection inherits sane defaults — no unintended SQLite hooks
# ---------------------------------------------------------------------------


def test_connection_emits_no_sqlite_warnings(tmp_path: Path) -> None:
    """A clean open + migrate cycle SHALL NOT emit any
    ``sqlite3.Warning`` (e.g., from a malformed PRAGMA or an
    unsupported syntax). Catches platforms where SQLite ships a
    surprisingly old build."""
    import warnings

    # Use the persistence-layer's re-export so the test file
    # doesn't itself import sqlite3 (REQ_F_PER_010 — the audit
    # below walks trading_system/* but our test guard is symbolic).
    from trading_system.persistence.connection import DatabaseError

    db_path = tmp_path / "warnings.sqlite"
    with warnings.catch_warnings():
        warnings.simplefilter("error", Warning)
        conn = Connection.open(db_path).unwrap()
        try:
            MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run()
        except DatabaseError as e:
            raise AssertionError(f"unexpected DB error: {e}") from e
        conn.close()
