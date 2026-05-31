"""CR-019 §6 follow-up — PaperSessionRepository tests.

REQ refs:
- REQ_F_PAP_003 (session metadata persistence).
- REQ_SDD_WEB2_005 (resume_from_persistence enrichment).
- REQ_F_PER_002 / REQ_F_PER_003 / REQ_F_PER_005 / REQ_F_PER_009.
- REQ_NF_PER_001 (round-trip determinism).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.money import Currency, Money
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.paper_sessions import (
    PaperSessionRepository,
    PaperSessionRow,
)
from trading_system.result import Err, Ok


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = (
    _REPO_ROOT / "trading_system" / "persistence" / "migrations"
)
_T0 = datetime(2026, 5, 31, 12, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "state.sqlite"
    connection = Connection.open(db_path).unwrap()
    MigrationRunner(conn=connection, migrations_dir=_BUNDLED_MIGRATIONS).run()
    yield connection
    connection.close()


def _row(account_id: str = "paper-2026-05-31T12:00:00+00:00") -> PaperSessionRow:
    return PaperSessionRow(
        account_id=AccountId(account_id),
        universe="cac40",
        strategy_id=StrategyId("CoreStrategy"),
        instrument_symbol="AC",
        starting_capital=Money(Decimal("10000"), Currency.EUR),
        bar_source="yfinance",
        started_at=_T0,
    )


# ---------------------------------------------------------------------------
# Migration audit
# ---------------------------------------------------------------------------


def test_migration_creates_paper_sessions_table(tmp_path: Path) -> None:
    """0010_paper_sessions.sql applies cleanly + the table carries
    the documented columns + the started_at index."""
    connection = Connection.open(tmp_path / "state.sqlite").unwrap()
    try:
        applied = MigrationRunner(
            conn=connection, migrations_dir=_BUNDLED_MIGRATIONS
        ).run().unwrap()
        assert "0010_paper_sessions.sql" in applied
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='paper_sessions'"
        ).fetchall()
        assert len(rows) == 1
        cols = connection.execute(
            "PRAGMA table_info(paper_sessions)"
        ).fetchall()
        col_names = {dict(c)["name"] for c in cols}
        assert col_names == {
            "account_id",
            "universe",
            "strategy_id",
            "instrument_symbol",
            "starting_capital",
            "currency",
            "bar_source",
            "started_at",
            "mode_tag",
        }
        idx_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_paper_sessions_started_at'"
        ).fetchall()
        assert len(idx_rows) == 1
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Write + read round-trip
# ---------------------------------------------------------------------------


def test_append_session_then_get_round_trips(conn) -> None:
    """REQ_NF_PER_001 — write then re-read yields the same Row."""
    repo = PaperSessionRepository(conn=conn)
    row = _row()
    assert isinstance(repo.append_session(row), Ok)
    res = repo.get(row.account_id)
    assert isinstance(res, Ok)
    assert res.value == row


def test_get_returns_none_for_unknown_account(conn) -> None:
    repo = PaperSessionRepository(conn=conn)
    res = repo.get(AccountId("paper-missing-2026"))
    assert isinstance(res, Ok)
    assert res.value is None


def test_append_session_duplicate_returns_integrity_err(conn) -> None:
    """REQ_F_PER_003 — re-writing the same account_id surfaces as
    the categorised integrity Err so the runtime can offer
    'stop existing first' instead of silently shadowing."""
    repo = PaperSessionRepository(conn=conn)
    row = _row()
    assert isinstance(repo.append_session(row), Ok)
    second = repo.append_session(row)
    assert isinstance(second, Err)
    assert "persistence:integrity:paper_sessions:duplicate" in second.error


# ---------------------------------------------------------------------------
# list_all + ordering
# ---------------------------------------------------------------------------


def test_list_all_returns_rows_sorted_by_started_at_desc(conn) -> None:
    """The recovery wizard renders most-recent first, so the
    repository sorts by started_at DESC."""
    repo = PaperSessionRepository(conn=conn)
    from datetime import timedelta

    r1 = _row(account_id="paper-2026-05-01T00:00:00+00:00")
    r2 = PaperSessionRow(
        account_id=AccountId("paper-2026-05-15T00:00:00+00:00"),
        universe="cac40",
        strategy_id=StrategyId("CoreStrategy"),
        instrument_symbol="AC",
        starting_capital=Money(Decimal("10000"), Currency.EUR),
        bar_source="yfinance",
        started_at=_T0 - timedelta(days=15),
    )
    r3 = PaperSessionRow(
        account_id=AccountId("paper-2026-05-31T00:00:00+00:00"),
        universe="cac40",
        strategy_id=StrategyId("CoreStrategy"),
        instrument_symbol="AC",
        starting_capital=Money(Decimal("10000"), Currency.EUR),
        bar_source="yfinance",
        started_at=_T0,
    )
    for r in (r1, r2, r3):
        repo.append_session(r)
    res = repo.list_all()
    assert isinstance(res, Ok)
    # r3 (most recent) first, r1 last.
    started_at_order = [row.started_at for row in res.value]
    assert started_at_order[0] == _T0
    # Reverse-chronological.
    assert started_at_order == sorted(started_at_order, reverse=True)


def test_list_all_empty_returns_ok_empty_tuple(conn) -> None:
    repo = PaperSessionRepository(conn=conn)
    res = repo.list_all()
    assert isinstance(res, Ok)
    assert res.value == ()


# ---------------------------------------------------------------------------
# Construction invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"universe": ""}, "universe must be non-empty"),
        ({"instrument_symbol": ""}, "instrument_symbol must be non-empty"),
        (
            {"starting_capital": Money(Decimal(0), Currency.EUR)},
            "starting_capital must be > 0",
        ),
        ({"bar_source": "fake"}, "bar_source must be one of"),
    ],
)
def test_paper_session_row_rejects_invalid_inputs(kwargs, match):
    base = {
        "account_id": AccountId("paper-2026-05-31T12:00:00+00:00"),
        "universe": "cac40",
        "strategy_id": StrategyId("CoreStrategy"),
        "instrument_symbol": "AC",
        "starting_capital": Money(Decimal("10000"), Currency.EUR),
        "bar_source": "yfinance",
        "started_at": _T0,
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        PaperSessionRow(**base)
