"""Tests for ``trading_system.persistence.repositories.portfolio``.

Covers TC_PER_005 (Decimal + datetime round-trip), TC_PER_006
(BEGIN IMMEDIATE rollback on integrity error), TC_PER_010
(account_id isolation), TC_PER_012 (WAL concurrent reader + writer).

REQ refs: REQ_F_PER_002, REQ_F_PER_003, REQ_F_PER_005,
REQ_F_PER_009, REQ_NF_PER_001, REQ_SDS_PER_002, REQ_SDS_PER_004,
REQ_SDD_PER_002, REQ_SDD_PER_003, REQ_SDD_PER_008.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId
from trading_system.models.money import Currency, Money
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.portfolio import PortfolioRepository
from trading_system.result import Err, Ok

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


def _migrated_conn(tmp_path: Path, name: str = "state.sqlite") -> Connection:
    conn = Connection.open(tmp_path / name).unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run()
    return conn


def _point(day: int, gross: str, net: str, dd: str = "0") -> EquityPoint:
    return EquityPoint(
        at=datetime(2026, 5, day, tzinfo=UTC),
        equity_gross=Money(Decimal(gross), Currency.EUR),
        equity_after_tax=Money(Decimal(net), Currency.EUR),
        drawdown_pct=Decimal(dd),
    )


# ---------------------------------------------------------------------------
# TC_PER_005 — Decimal + datetime round-trip
# ---------------------------------------------------------------------------


def test_round_trip_preserves_decimal_and_datetime(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = PortfolioRepository(conn=conn)
    point = _point(8, "100000.123456789012345", "70000.987654321098765", "0.001234567")
    assert isinstance(repo.append_equity_point(point), Ok)
    curve = repo.equity_curve().unwrap()
    assert len(curve) == 1
    loaded = curve[0]
    # Bit-identical structural equality (REQ_NF_PER_001).
    assert loaded == point
    # Decimal precision preserved exactly.
    assert loaded.equity_gross.amount == Decimal("100000.123456789012345")
    assert loaded.equity_after_tax.amount == Decimal("70000.987654321098765")
    # Datetime carries tzinfo.
    assert loaded.at.tzinfo is not None


def test_multiple_points_returned_in_at_order(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = PortfolioRepository(conn=conn)
    # Insert out of order.
    repo.append_equity_point(_point(10, "11000", "8000"))
    repo.append_equity_point(_point(8, "10000", "7000"))
    repo.append_equity_point(_point(9, "10500", "7500"))
    curve = repo.equity_curve().unwrap()
    days = [p.at.day for p in curve]
    assert days == [8, 9, 10]


# ---------------------------------------------------------------------------
# TC_PER_006 — write failure leaves DB unchanged
# ---------------------------------------------------------------------------


def test_duplicate_at_returns_integrity_err_and_rolls_back(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = PortfolioRepository(conn=conn)
    repo.append_equity_point(_point(8, "10000", "7000"))
    # Second insert at the same (account_id, at) violates PK.
    before = conn.execute("SELECT COUNT(*) AS n FROM equity_points").fetchone()["n"]
    match repo.append_equity_point(_point(8, "11000", "7500")):
        case Err(reason):
            assert reason.startswith("persistence:integrity:equity_points")
        case Ok(_):
            raise AssertionError("expected Err on duplicate (account_id, at)")
    after = conn.execute("SELECT COUNT(*) AS n FROM equity_points").fetchone()["n"]
    assert after == before, "row count must not change after a failed insert"


# ---------------------------------------------------------------------------
# TC_PER_010 — account_id isolation
# ---------------------------------------------------------------------------


def test_cross_account_isolation(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = PortfolioRepository(conn=conn)
    repo.append_equity_point(_point(8, "10000", "7000"), account_id=DEFAULT_ACCOUNT_ID)
    other = AccountId("alt")
    repo.append_equity_point(_point(8, "20000", "14000"), account_id=other)
    default_curve = repo.equity_curve().unwrap()
    alt_curve = repo.equity_curve(account_id=other).unwrap()
    assert len(default_curve) == 1
    assert len(alt_curve) == 1
    assert default_curve[0].equity_gross.amount == Decimal("10000")
    assert alt_curve[0].equity_gross.amount == Decimal("20000")
    # Reading a non-existent account_id returns an empty curve, not
    # the default account's rows.
    empty = repo.equity_curve(account_id=AccountId("ghost")).unwrap()
    assert empty == ()


# ---------------------------------------------------------------------------
# TC_PER_012 — WAL concurrent reader + writer
# ---------------------------------------------------------------------------


def test_wal_lets_reader_see_committed_write(tmp_path: Path) -> None:
    """One writer commits; a second connection then sees the
    committed row. (We don't test concurrent-in-flight writes —
    the WAL guarantee here is that committed writes become visible
    to fresh reads from another connection.)"""
    db = tmp_path / "state.sqlite"
    writer_conn = Connection.open(db).unwrap()
    MigrationRunner(conn=writer_conn, migrations_dir=_BUNDLED_MIGRATIONS).run()
    writer_repo = PortfolioRepository(conn=writer_conn)

    reader_conn = Connection.open(db).unwrap()
    reader_repo = PortfolioRepository(conn=reader_conn)

    # Initial: both see empty.
    assert reader_repo.equity_curve().unwrap() == ()

    # Writer commits a row.
    writer_repo.append_equity_point(_point(8, "10000", "7000"))

    # Reader sees it (WAL semantics + autocommit reads).
    seen = reader_repo.equity_curve().unwrap()
    assert len(seen) == 1
    assert seen[0].equity_gross.amount == Decimal("10000")

    writer_conn.close()
    reader_conn.close()
