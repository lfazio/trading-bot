"""CR-019 step 2 / TC_LIV_010..012 — LiveOrderRepository tests.

REQ refs: REQ_F_LIV_007, REQ_SDD_LIV_003, REQ_SDD_LIV_006,
REQ_F_PER_002 / 003 / 004 / 009, REQ_NF_PER_001, REQ_SDS_PER_002.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_system.models.identifiers import AccountId, OrderId
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.live_orders import (
    LiveOrderRepository,
    LiveOrderRow,
    LiveOrderStatus,
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
# TC_LIV_010 — migration audit
# ---------------------------------------------------------------------------


def test_migration_creates_table_with_expected_schema(tmp_path: Path) -> None:
    """0008_live_orders.sql applies cleanly + the expected columns +
    indexes are present. Re-running is idempotent (SHA-lock holds)."""
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    runner = MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS)
    applied = runner.run().unwrap()
    assert "0008_live_orders.sql" in applied
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='live_orders'"
    ).fetchall()
    assert len(rows) == 1
    cols = conn.execute("PRAGMA table_info(live_orders)").fetchall()
    names = {col["name"] for col in cols}
    assert names == {
        "account_id",
        "order_id",
        "broker_selector",
        "broker_order_id",
        "submitted_at",
        "submitted_order_json",
        "corr_id",
        "status",
        "rejection_reason",
    }
    # Indexes present.
    idx_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='live_orders'"
    ).fetchall()
    idx_names = {row["name"] for row in idx_rows}
    assert "idx_live_orders_submitted_at" in idx_names
    assert "idx_live_orders_status" in idx_names
    # Re-run is idempotent.
    again = runner.run().unwrap()
    assert "0008_live_orders.sql" not in again
    conn.close()


# ---------------------------------------------------------------------------
# TC_LIV_011 — round-trip happy path
# ---------------------------------------------------------------------------


def test_record_submit_intent_then_submitted_round_trip(
    conn: Connection,
) -> None:
    repo = LiveOrderRepository(conn=conn)
    result = repo.record_submit_intent(
        order_id=OrderId("o-001"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json='{"symbol": "ASML.AS", "side": "buy"}',
        corr_id="trace-abc",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    assert isinstance(result, Ok)
    # Row exists in pending.
    pending = repo.list_pending(account_id=AccountId("live-alpha")).unwrap()
    assert len(pending) == 1
    assert pending[0].order_id == OrderId("o-001")
    assert pending[0].status is LiveOrderStatus.PENDING
    assert pending[0].broker_order_id is None
    # Flip to submitted.
    flip = repo.record_submitted(
        order_id=OrderId("o-001"),
        broker_order_id="broker-handle-xyz",
        account_id=AccountId("live-alpha"),
    )
    assert isinstance(flip, Ok)
    # Row carries the broker_order_id + status flipped.
    fetched = repo.get(
        order_id=OrderId("o-001"), account_id=AccountId("live-alpha")
    ).unwrap()
    assert isinstance(fetched, LiveOrderRow)
    assert fetched.status is LiveOrderStatus.SUBMITTED
    assert fetched.broker_order_id == "broker-handle-xyz"
    # Pending list is now empty.
    pending = repo.list_pending(account_id=AccountId("live-alpha")).unwrap()
    assert pending == ()


def test_record_rejected_path(conn: Connection) -> None:
    repo = LiveOrderRepository(conn=conn)
    repo.record_submit_intent(
        order_id=OrderId("o-002"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json='{"symbol": "X", "side": "buy"}',
        corr_id="trace-def",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    result = repo.record_rejected(
        order_id=OrderId("o-002"),
        rejection_reason="broker:insufficient_funds",
        account_id=AccountId("live-alpha"),
    )
    assert isinstance(result, Ok)
    fetched = repo.get(
        order_id=OrderId("o-002"), account_id=AccountId("live-alpha")
    ).unwrap()
    assert isinstance(fetched, LiveOrderRow)
    assert fetched.status is LiveOrderStatus.REJECTED
    assert fetched.rejection_reason == "broker:insufficient_funds"


# ---------------------------------------------------------------------------
# Invariants + bad input
# ---------------------------------------------------------------------------


def test_empty_order_id_rejected(conn: Connection) -> None:
    repo = LiveOrderRepository(conn=conn)
    result = repo.record_submit_intent(
        order_id=OrderId(""),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    assert isinstance(result, Err)
    assert "empty_order_id" in result.error


def test_empty_broker_selector_rejected(conn: Connection) -> None:
    repo = LiveOrderRepository(conn=conn)
    result = repo.record_submit_intent(
        order_id=OrderId("o-1"),
        account_id=AccountId("live-alpha"),
        broker_selector="",
        submitted_order_json="{}",
        corr_id="trace",
    )
    assert isinstance(result, Err)
    assert "empty_broker_selector" in result.error


def test_record_submitted_empty_broker_order_id_rejected(
    conn: Connection,
) -> None:
    repo = LiveOrderRepository(conn=conn)
    repo.record_submit_intent(
        order_id=OrderId("o-3"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    result = repo.record_submitted(
        order_id=OrderId("o-3"),
        broker_order_id="",
        account_id=AccountId("live-alpha"),
    )
    assert isinstance(result, Err)
    assert "empty_broker_order_id" in result.error


def test_record_rejected_empty_reason_rejected(conn: Connection) -> None:
    repo = LiveOrderRepository(conn=conn)
    repo.record_submit_intent(
        order_id=OrderId("o-4"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    result = repo.record_rejected(
        order_id=OrderId("o-4"),
        rejection_reason="",
        account_id=AccountId("live-alpha"),
    )
    assert isinstance(result, Err)
    assert "empty_rejection_reason" in result.error


def test_record_submitted_on_missing_order_returns_not_found(
    conn: Connection,
) -> None:
    repo = LiveOrderRepository(conn=conn)
    result = repo.record_submitted(
        order_id=OrderId("ghost"),
        broker_order_id="x",
        account_id=AccountId("live-alpha"),
    )
    assert isinstance(result, Err)
    assert result.error.startswith("persistence:not_found:")


def test_record_rejected_on_missing_order_returns_not_found(
    conn: Connection,
) -> None:
    repo = LiveOrderRepository(conn=conn)
    result = repo.record_rejected(
        order_id=OrderId("ghost"),
        rejection_reason="x",
        account_id=AccountId("live-alpha"),
    )
    assert isinstance(result, Err)
    assert result.error.startswith("persistence:not_found:")


def test_duplicate_order_id_surfaces_integrity_err(conn: Connection) -> None:
    repo = LiveOrderRepository(conn=conn)
    repo.record_submit_intent(
        order_id=OrderId("o-5"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    result = repo.record_submit_intent(
        order_id=OrderId("o-5"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    assert isinstance(result, Err)
    assert result.error.startswith("persistence:integrity:live_orders:")


def test_get_missing_returns_ok_none(conn: Connection) -> None:
    repo = LiveOrderRepository(conn=conn)
    result = repo.get(order_id=OrderId("ghost"), account_id=AccountId("live-alpha"))
    assert isinstance(result, Ok)
    assert result.value is None


def test_cross_account_isolation_on_list_pending(conn: Connection) -> None:
    repo = LiveOrderRepository(conn=conn)
    repo.record_submit_intent(
        order_id=OrderId("o-a"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    repo.record_submit_intent(
        order_id=OrderId("o-b"),
        account_id=AccountId("live-beta"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    alpha = repo.list_pending(account_id=AccountId("live-alpha")).unwrap()
    beta = repo.list_pending(account_id=AccountId("live-beta")).unwrap()
    assert [r.order_id for r in alpha] == [OrderId("o-a")]
    assert [r.order_id for r in beta] == [OrderId("o-b")]


def test_pending_list_sorted_by_submitted_at(conn: Connection) -> None:
    repo = LiveOrderRepository(conn=conn)
    repo.record_submit_intent(
        order_id=OrderId("late"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
        now=datetime(2026, 5, 27, 12, tzinfo=UTC),
    )
    repo.record_submit_intent(
        order_id=OrderId("early"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    pending = repo.list_pending(account_id=AccountId("live-alpha")).unwrap()
    assert [r.order_id for r in pending] == [OrderId("early"), OrderId("late")]


# ---------------------------------------------------------------------------
# TC_LIV_012 — Err-category coverage (proxy-injection pattern)
# ---------------------------------------------------------------------------


class _RaisingExecProxy:
    def __init__(self, real, when, exc):
        self._real = real
        self._when = when
        self._exc = exc

    def execute(self, sql, *args, **kwargs):
        if self._when(sql):
            raise self._exc
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _install(conn, monkeypatch, *, when, exc) -> None:
    monkeypatch.setattr(conn, "_raw", _RaisingExecProxy(conn._raw, when, exc))


def test_record_submit_intent_operational_error_surfaces_locked(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import OperationalError

    repo = LiveOrderRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO live_orders" in sql,
        exc=OperationalError("database is locked"),
    )
    result = repo.record_submit_intent(
        order_id=OrderId("o-locked"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    assert isinstance(result, Err)
    assert result.error.startswith("persistence:locked:live_orders:")


def test_record_submit_intent_corrupt_error_surfaces(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    repo = LiveOrderRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO live_orders" in sql,
        exc=DatabaseError("disk corrupt"),
    )
    result = repo.record_submit_intent(
        order_id=OrderId("o-corrupt"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    assert isinstance(result, Err)
    assert result.error.startswith("persistence:corrupt:live_orders:")


def test_record_submitted_db_error_surfaces(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    repo = LiveOrderRepository(conn=conn)
    # First insert successfully.
    repo.record_submit_intent(
        order_id=OrderId("o-up"),
        account_id=AccountId("live-alpha"),
        broker_selector="xtb",
        submitted_order_json="{}",
        corr_id="trace",
    )
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "UPDATE live_orders" in sql,
        exc=DatabaseError("update failed"),
    )
    result = repo.record_submitted(
        order_id=OrderId("o-up"),
        broker_order_id="x",
        account_id=AccountId("live-alpha"),
    )
    assert isinstance(result, Err)
    assert result.error.startswith("persistence:corrupt:live_orders:update:")


def test_list_pending_db_error_surfaces(conn: Connection, monkeypatch) -> None:
    from trading_system.persistence.connection import DatabaseError

    repo = LiveOrderRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "FROM live_orders" in sql,
        exc=DatabaseError("read failed"),
    )
    result = repo.list_pending(account_id=AccountId("live-alpha"))
    assert isinstance(result, Err)
    assert result.error.startswith("persistence:corrupt:live_orders:read:")


# ---------------------------------------------------------------------------
# Dataclass invariants
# ---------------------------------------------------------------------------


def test_live_order_row_rejects_empty_order_id() -> None:
    with pytest.raises(ValueError, match="order_id"):
        LiveOrderRow(
            account_id=AccountId("live-alpha"),
            order_id=OrderId(""),
            broker_selector="xtb",
            submitted_at=datetime(2026, 5, 26, tzinfo=UTC),
            submitted_order_json="{}",
            corr_id="x",
            status=LiveOrderStatus.PENDING,
        )


def test_live_order_row_rejects_empty_broker_selector() -> None:
    with pytest.raises(ValueError, match="broker_selector"):
        LiveOrderRow(
            account_id=AccountId("live-alpha"),
            order_id=OrderId("o-1"),
            broker_selector="",
            submitted_at=datetime(2026, 5, 26, tzinfo=UTC),
            submitted_order_json="{}",
            corr_id="x",
            status=LiveOrderStatus.PENDING,
        )


def test_live_order_status_string_values() -> None:
    """Lock down the canonical strings — the persistence layer
    round-trips on these."""
    assert LiveOrderStatus.PENDING.value == "pending"
    assert LiveOrderStatus.SUBMITTED.value == "submitted"
    assert LiveOrderStatus.REJECTED.value == "rejected"
