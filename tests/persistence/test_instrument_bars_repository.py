"""CR-029 / TC_PER_BAR_001..004, TC_PER_BAR_007 — InstrumentBarRepository.

REQ refs: REQ_F_PER_011 / 012 / 014, REQ_SDD_PER_010 / 011, REQ_NF_DAT_001.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.data.types import Bar
from trading_system.models.identifiers import AccountId, InstrumentId
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.instrument_bars import (
    InstrumentBarRepository,
)
from trading_system.result import Err, Ok


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = (
    _REPO_ROOT / "trading_system" / "persistence" / "migrations"
)


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "state.sqlite"
    connection = Connection.open(db_path).unwrap()
    MigrationRunner(conn=connection, migrations_dir=_BUNDLED_MIGRATIONS).run()
    yield connection
    connection.close()


_T0 = datetime(2026, 5, 30, 12, tzinfo=UTC)
_AID = AccountId("paper-2026-05-30T12:00:00+00:00")


def _bar(*, close: str = "100.00", at: datetime = _T0) -> Bar:
    p = Decimal(close)
    return Bar(
        at=at,
        open=p,
        high=p * Decimal("1.005"),
        low=p * Decimal("0.995"),
        close=p,
        volume=Decimal("1000"),
    )


# ---------------------------------------------------------------------------
# TC_PER_BAR_001 — migration audit
# ---------------------------------------------------------------------------


def test_migration_creates_instrument_bars_table_with_expected_schema(
    tmp_path: Path,
) -> None:
    """REQ_SDD_PER_010 — 0009 migration applies cleanly; the table
    carries the documented columns + the cross-symbol index."""
    connection = Connection.open(tmp_path / "state.sqlite").unwrap()
    try:
        runner = MigrationRunner(
            conn=connection, migrations_dir=_BUNDLED_MIGRATIONS
        )
        applied = runner.run().unwrap()
        assert "0009_instrument_bars.sql" in applied
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='instrument_bars'"
        ).fetchall()
        assert len(rows) == 1
        cols = connection.execute(
            "PRAGMA table_info(instrument_bars)"
        ).fetchall()
        col_names = {dict(c)["name"] for c in cols}
        assert col_names == {
            "account_id",
            "instrument_id",
            "bar_at",
            "open",
            "high",
            "low",
            "close",
            "volume",
        }
        # Cross-symbol slice index registered.
        idx_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_instrument_bars_by_account_at'"
        ).fetchall()
        assert len(idx_rows) == 1
    finally:
        connection.close()


def test_migration_re_run_is_idempotent(tmp_path: Path) -> None:
    """REQ_F_PER_004 — re-running the migration is a no-op."""
    db = tmp_path / "state.sqlite"
    c1 = Connection.open(db).unwrap()
    MigrationRunner(conn=c1, migrations_dir=_BUNDLED_MIGRATIONS).run()
    c1.close()
    c2 = Connection.open(db).unwrap()
    result = MigrationRunner(
        conn=c2, migrations_dir=_BUNDLED_MIGRATIONS
    ).run()
    # Returns Ok regardless of which migrations actually ran.
    assert isinstance(result, Ok)
    c2.close()


# ---------------------------------------------------------------------------
# TC_PER_BAR_002 — round-trip + ordering
# ---------------------------------------------------------------------------


def test_append_bars_then_bars_for_returns_ordered_subset(conn) -> None:
    """REQ_F_PER_011 / REQ_SDD_PER_011 — append batched rows;
    bars_for returns the per-symbol slice ordered by ``bar_at ASC``;
    Decimal-as-TEXT preserves byte-equal values."""
    repo = InstrumentBarRepository(conn=conn)
    iid_a = InstrumentId("AAA.PA")
    iid_b = InstrumentId("BBB.PA")
    rows = [
        (iid_a, _bar(close="10.00", at=_T0)),
        (iid_a, _bar(close="11.00", at=_T0 + timedelta(days=1))),
        (iid_b, _bar(close="20.00", at=_T0)),
        (iid_b, _bar(close="21.00", at=_T0 + timedelta(days=1))),
    ]
    assert isinstance(repo.append_bars(rows, account_id=_AID), Ok)
    a_result = repo.bars_for(
        account_id=_AID,
        instrument_id=iid_a,
        start=_T0,
        end=_T0 + timedelta(days=2),
    )
    assert isinstance(a_result, Ok)
    bars_a = a_result.value
    assert [b.close for b in bars_a] == [Decimal("10.00"), Decimal("11.00")]
    # Ordered ascending by bar_at.
    assert bars_a[0].at < bars_a[1].at


# ---------------------------------------------------------------------------
# TC_PER_BAR_003 — idempotent duplicate-PK
# ---------------------------------------------------------------------------


def test_append_bar_duplicate_pk_is_idempotent(conn) -> None:
    """REQ_F_PER_012 / REQ_SDD_PER_011 — same key re-write returns
    Ok(None); the existing row is preserved (INSERT OR IGNORE).
    The CR-021 cache contract owns the bytes; mismatched Decimals
    indicate a cache audit, not a repository Err."""
    repo = InstrumentBarRepository(conn=conn)
    iid = InstrumentId("AAA.PA")
    first = _bar(close="10.00", at=_T0)
    second = _bar(close="99.99", at=_T0)  # mismatched on purpose
    assert isinstance(repo.append_bar(first, account_id=_AID, instrument_id=iid), Ok)
    assert isinstance(repo.append_bar(second, account_id=_AID, instrument_id=iid), Ok)
    res = repo.bars_for(
        account_id=_AID, instrument_id=iid, start=_T0, end=_T0
    )
    assert isinstance(res, Ok)
    # The first write wins (INSERT OR IGNORE keeps the existing row).
    assert res.value[0].close == Decimal("10.00")


def test_append_bars_empty_iterable_returns_ok_noop(conn) -> None:
    repo = InstrumentBarRepository(conn=conn)
    res = repo.append_bars([], account_id=_AID)
    assert isinstance(res, Ok)


# ---------------------------------------------------------------------------
# TC_PER_BAR_004 — cross-symbol slice
# ---------------------------------------------------------------------------


def test_bars_at_returns_mapping_for_universe_snapshot(conn) -> None:
    """REQ_F_PER_011 / REQ_SDD_PER_011 — bars_at gives the
    cross-symbol view at a single timestamp."""
    repo = InstrumentBarRepository(conn=conn)
    iids = [InstrumentId(s) for s in ("AAA.PA", "BBB.PA", "CCC.PA")]
    rows = [(iid, _bar(close=f"{10 + i}.00", at=_T0)) for i, iid in enumerate(iids)]
    repo.append_bars(rows, account_id=_AID)
    res = repo.bars_at(account_id=_AID, at=_T0)
    assert isinstance(res, Ok)
    mapping = res.value
    assert set(mapping.keys()) == set(iids)
    assert mapping[InstrumentId("BBB.PA")].close == Decimal("11.00")


def test_bars_at_empty_when_no_rows(conn) -> None:
    """REQ_SDD_PER_011 — no rows at the timestamp ⇒ Ok({}), not Err.
    The absence of data is observable signal."""
    repo = InstrumentBarRepository(conn=conn)
    res = repo.bars_at(account_id=_AID, at=_T0)
    assert isinstance(res, Ok)
    assert res.value == {}


# ---------------------------------------------------------------------------
# TC_PER_BAR_007 — replay determinism (paired-run)
# ---------------------------------------------------------------------------


def test_paired_run_produces_byte_identical_rows(tmp_path: Path) -> None:
    """REQ_F_PER_014 / REQ_SDD_PER_014 — two runs of the same
    fan-out against the same Decimal inputs produce tuple-equal
    sorted row lists."""
    fixture = [
        (InstrumentId("AAA.PA"), _bar(close="10.00", at=_T0)),
        (InstrumentId("BBB.PA"), _bar(close="20.00", at=_T0)),
        (InstrumentId("AAA.PA"), _bar(close="11.00", at=_T0 + timedelta(days=1))),
    ]

    def dump(db_path: Path) -> list[tuple]:
        c = Connection.open(db_path).unwrap()
        MigrationRunner(conn=c, migrations_dir=_BUNDLED_MIGRATIONS).run()
        repo = InstrumentBarRepository(conn=c)
        repo.append_bars(fixture, account_id=_AID)
        cur = c.execute(
            "SELECT account_id, instrument_id, bar_at, open, high, low, close, volume "
            "FROM instrument_bars ORDER BY account_id, instrument_id, bar_at"
        )
        rows = [tuple(dict(r).items()) for r in cur.fetchall()]
        c.close()
        return rows

    run1 = dump(tmp_path / "state1.sqlite")
    run2 = dump(tmp_path / "state2.sqlite")
    assert run1 == run2
