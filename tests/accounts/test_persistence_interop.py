"""Tests for the CR-006 / CR-008 persistence-layer interop
(TC_ACC_010).

The persistence layer already carries ``account_id`` columns
(REQ_F_PER_009 / REQ_SDD_PER_008) so adding CR-006's runtime is a
code-only change at the repository call sites — no schema migration
required. This test asserts that property by:

  1. Running the existing CR-008 migration set.
  2. Writing an equity-curve row with the legacy default account.
  3. Writing the same row under a non-default account_id.
  4. Verifying that reads from each account_id see their own rows,
     and that DEFAULT_ACCOUNT_ID does NOT leak into other accounts.
  5. Verifying that running the migration runner against an already-
     migrated database is a no-op (the foundation SHALL accept
     CR-006 callers without re-migrating).

REQ refs: REQ_F_ACC_001, REQ_F_PER_009, REQ_SDD_ACC_001,
REQ_SDD_PER_008.
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
from trading_system.persistence.repositories.portfolio import (
    PortfolioRepository,
)
from trading_system.result import Ok

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


def _point(day: int, *, amount: str = "10000") -> EquityPoint:
    return EquityPoint(
        at=datetime(2026, 5, day, tzinfo=UTC),
        equity_gross=Money(Decimal(amount), Currency.EUR),
        equity_after_tax=Money(Decimal(amount), Currency.EUR),
        drawdown_pct=Decimal("0.0"),
    )


def test_persistence_writes_default_when_account_id_omitted(tmp_path: Path) -> None:
    """An account-aware write that omits ``account_id`` lands as the
    DEFAULT_ACCOUNT_ID sentinel (REQ_F_PER_009 / REQ_NF_ACC_001)."""
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run().unwrap()
    repo = PortfolioRepository(conn=conn)
    assert isinstance(repo.append_equity_point(_point(8)), Ok)
    # Read with explicit DEFAULT_ACCOUNT_ID returns the row.
    curve = repo.equity_curve(account_id=DEFAULT_ACCOUNT_ID).unwrap()
    assert len(curve) == 1


def test_cross_account_writes_isolated(tmp_path: Path) -> None:
    """Two accounts writing to the same instrument's curve SHALL see
    only their own rows (REQ_SDD_ACC_001 / REQ_SDD_PER_008)."""
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run().unwrap()
    repo = PortfolioRepository(conn=conn)
    alt = AccountId("alt")
    repo.append_equity_point(_point(8, amount="10000"), account_id=DEFAULT_ACCOUNT_ID)
    repo.append_equity_point(_point(8, amount="50000"), account_id=alt)
    default_curve = repo.equity_curve(account_id=DEFAULT_ACCOUNT_ID).unwrap()
    alt_curve = repo.equity_curve(account_id=alt).unwrap()
    assert default_curve[0].equity_gross.amount == Decimal("10000")
    assert alt_curve[0].equity_gross.amount == Decimal("50000")
    # A read against a third account_id sees nothing — not the default's rows.
    ghost = repo.equity_curve(account_id=AccountId("ghost")).unwrap()
    assert ghost == ()


def test_phase_6_requires_no_schema_migration(tmp_path: Path) -> None:
    """REQ_F_PER_009 / REQ_SDD_PER_008 — adding CR-006 code to call
    sites in Phase 6 SHALL NOT require a schema migration. Verified
    by running the bundled migrations twice and asserting the second
    pass is a no-op (zero new migration files applied)."""
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    runner = MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS)
    first = runner.run().unwrap()
    second = runner.run().unwrap()
    assert first != []
    assert second == []
