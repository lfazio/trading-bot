"""``SqliteIdempotencyStore`` tests — CR-004 Phase B
(REQ_F_WEB_010 / REQ_SDD_WEB_004 / REQ_SDS_WEB_004).

The SQLite backend SHALL satisfy the same ``IdempotencyStore``
Protocol as the in-memory backend so the route layer's call site
stays unchanged. Replays with the same key SHALL return the
prior body byte-identically; divergent body ⇒
``webui:idempotency_conflict``; expired entries swept lazily on
lookup.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_system.models.identifiers import AccountId
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories import SqliteIdempotencyStore
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.webui.idempotency import IdempotencyStore


_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "trading_system"
    / "persistence"
    / "migrations"
)


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "test.db"
    connection = Connection.open(db_path).unwrap()
    runner = MigrationRunner(conn=connection, migrations_dir=_MIGRATIONS_DIR)
    runner.run().unwrap()
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_satisfies_idempotency_store_protocol(conn: Connection) -> None:
    """REQ_F_WEB_010 — SQLite backend is a drop-in for the
    in-memory store; the existing IdempotencyStore Protocol
    runtime-check SHALL pass."""
    store = SqliteIdempotencyStore(conn=conn)
    assert isinstance(store, IdempotencyStore)


# ---------------------------------------------------------------------------
# Lookup + record round-trip
# ---------------------------------------------------------------------------


def test_lookup_missing_returns_nothing(conn: Connection) -> None:
    store = SqliteIdempotencyStore(conn=conn)
    match store.lookup(account_id=AccountId("default"), key="abc"):
        case Ok(Nothing()):
            pass
        case _:
            raise AssertionError("expected Ok(Nothing())")


def test_record_then_lookup_returns_some(conn: Connection) -> None:
    store = SqliteIdempotencyStore(conn=conn)
    body = '{"promoted":true,"strategy_id":"alpha"}'
    store.record(
        account_id=AccountId("default"),
        key="k-1",
        body=body,
        status_code=200,
    ).unwrap()
    match store.lookup(account_id=AccountId("default"), key="k-1"):
        case Ok(Some(prior)):
            assert prior == body
        case _:
            raise AssertionError("expected Ok(Some(body))")


def test_status_code_for_round_trip(conn: Connection) -> None:
    store = SqliteIdempotencyStore(conn=conn)
    store.record(
        account_id=AccountId("default"), key="k-1", body="{}", status_code=202
    ).unwrap()
    assert store.status_code_for(account_id=AccountId("default"), key="k-1") == 202


# ---------------------------------------------------------------------------
# Conflict / bad-key — REQ_F_WEB_008 family
# ---------------------------------------------------------------------------


def test_divergent_body_for_same_key_returns_conflict(conn: Connection) -> None:
    """REQ_F_WEB_008 — replays with the same key SHALL return the
    original response. A divergent body for a known key is a
    programmer-error surface."""
    store = SqliteIdempotencyStore(conn=conn)
    store.record(
        account_id=AccountId("default"),
        key="k-1",
        body='{"a":1}',
        status_code=200,
    ).unwrap()
    match store.record(
        account_id=AccountId("default"),
        key="k-1",
        body='{"a":2}',  # different
        status_code=200,
    ):
        case Err(reason):
            assert reason == "webui:idempotency_conflict"
        case _:
            raise AssertionError("expected Err(webui:idempotency_conflict)")


def test_re_record_same_body_is_idempotent(conn: Connection) -> None:
    store = SqliteIdempotencyStore(conn=conn)
    store.record(
        account_id=AccountId("default"),
        key="k-1",
        body="{}",
        status_code=200,
    ).unwrap()
    # Same body ⇒ Ok; no conflict.
    match store.record(
        account_id=AccountId("default"),
        key="k-1",
        body="{}",
        status_code=200,
    ):
        case Ok(_):
            pass
        case _:
            raise AssertionError("expected Ok on idempotent re-record")


def test_empty_key_returns_bad_key_err(conn: Connection) -> None:
    store = SqliteIdempotencyStore(conn=conn)
    match store.record(
        account_id=AccountId("default"), key="   ", body="{}", status_code=200
    ):
        case Err(reason):
            assert reason == "webui:idempotency_bad_key"
        case _:
            raise AssertionError("expected webui:idempotency_bad_key")


# ---------------------------------------------------------------------------
# TTL — REQ_SDS_WEB_004
# ---------------------------------------------------------------------------


def test_lookup_after_ttl_returns_nothing(conn: Connection) -> None:
    """Expired entries SHALL be swept lazily on lookup."""
    clock = {"now": datetime(2026, 5, 18, 12, 0, tzinfo=UTC)}

    def now() -> datetime:
        return clock["now"]

    store = SqliteIdempotencyStore(conn=conn, ttl_seconds=60, now=now)
    store.record(
        account_id=AccountId("default"),
        key="k-1",
        body="{}",
        status_code=200,
    ).unwrap()
    # Within TTL.
    match store.lookup(account_id=AccountId("default"), key="k-1"):
        case Ok(Some(_)):
            pass
        case _:
            raise AssertionError("expected Some within TTL")
    # Advance clock past TTL.
    clock["now"] = clock["now"] + timedelta(seconds=61)
    match store.lookup(account_id=AccountId("default"), key="k-1"):
        case Ok(Nothing()):
            pass
        case _:
            raise AssertionError("expected Nothing after TTL")


def test_sweep_expired_returns_row_count(conn: Connection) -> None:
    clock = {"now": datetime(2026, 5, 18, 12, 0, tzinfo=UTC)}

    def now() -> datetime:
        return clock["now"]

    store = SqliteIdempotencyStore(conn=conn, ttl_seconds=60, now=now)
    for i in range(3):
        store.record(
            account_id=AccountId("default"),
            key=f"k-{i}",
            body=f"{{\"i\":{i}}}",
            status_code=200,
        ).unwrap()
    # Advance past TTL — every row is now expired.
    clock["now"] = clock["now"] + timedelta(seconds=120)
    match store.sweep_expired(account_id=AccountId("default")):
        case Ok(removed):
            assert removed == 3
        case _:
            raise AssertionError("expected Ok(3 rows removed)")


# ---------------------------------------------------------------------------
# Account isolation — REQ_F_PER_009
# ---------------------------------------------------------------------------


def test_same_key_isolated_across_accounts(conn: Connection) -> None:
    store = SqliteIdempotencyStore(conn=conn)
    store.record(
        account_id=AccountId("alpha"), key="k", body='{"v":"alpha"}',
        status_code=200,
    ).unwrap()
    store.record(
        account_id=AccountId("beta"), key="k", body='{"v":"beta"}',
        status_code=200,
    ).unwrap()
    match store.lookup(account_id=AccountId("alpha"), key="k"):
        case Ok(Some(body)):
            assert "alpha" in body
        case _:
            raise AssertionError("alpha lookup failed")
    match store.lookup(account_id=AccountId("beta"), key="k"):
        case Ok(Some(body)):
            assert "beta" in body
        case _:
            raise AssertionError("beta lookup failed")


# ---------------------------------------------------------------------------
# Phase-8 C1 — Err-branch coverage (DB exception paths)
# ---------------------------------------------------------------------------


class _RaisingExecProxy:
    """Proxy raising ``exc`` on a matching SQL; otherwise delegates."""

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


def test_constructor_rejects_non_positive_ttl(conn: Connection) -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be > 0"):
        SqliteIdempotencyStore(conn=conn, ttl_seconds=0)
    with pytest.raises(ValueError, match="ttl_seconds must be > 0"):
        SqliteIdempotencyStore(conn=conn, ttl_seconds=-1)


def test_lookup_database_error_surfaces_categorised_err(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    store = SqliteIdempotencyStore(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "SELECT body, recorded_at" in sql,
        exc=DatabaseError("read failed"),
    )
    match store.lookup(account_id=AccountId("default"), key="k"):
        case Err(reason):
            assert reason.startswith(
                "persistence:corrupt:idempotency_entries:read:"
            )
        case _:
            raise AssertionError("expected Err")


def test_record_lookup_database_error_surfaces_err(
    conn: Connection, monkeypatch
) -> None:
    """`record` calls a pre-read to detect divergent bodies; a
    DatabaseError on THAT lookup SHALL surface as
    `persistence:corrupt:idempotency_entries:read:<reason>`."""
    from trading_system.persistence.connection import DatabaseError

    store = SqliteIdempotencyStore(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "SELECT body FROM idempotency_entries" in sql,
        exc=DatabaseError("read failed"),
    )
    match store.record(
        account_id=AccountId("default"), key="k", body="b", status_code=200
    ):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:idempotency_entries:read:")
        case _:
            raise AssertionError("expected Err")


def test_record_write_database_error_surfaces_err(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    store = SqliteIdempotencyStore(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO idempotency_entries" in sql,
        exc=DatabaseError("disk corrupt"),
    )
    match store.record(
        account_id=AccountId("default"), key="k", body="b", status_code=200
    ):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:idempotency_entries:write:")
        case _:
            raise AssertionError("expected Err")


def test_status_code_for_database_error_returns_none(
    conn: Connection, monkeypatch
) -> None:
    """`status_code_for` is the convenience accessor; on
    DatabaseError it SHALL return None (swallow the error rather
    than propagating)."""
    from trading_system.persistence.connection import DatabaseError

    store = SqliteIdempotencyStore(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "SELECT status_code" in sql,
        exc=DatabaseError("read failed"),
    )
    assert store.status_code_for(account_id=AccountId("default"), key="k") is None


def test_status_code_for_missing_returns_none(conn: Connection) -> None:
    store = SqliteIdempotencyStore(conn=conn)
    assert store.status_code_for(account_id=AccountId("default"), key="ghost") is None


def test_lookup_expired_delete_failure_surfaces_err(
    conn: Connection, monkeypatch
) -> None:
    """When a lookup finds an expired row + the lazy DELETE fails,
    the Err SHALL surface as
    `persistence:corrupt:idempotency_entries:expired_delete:<reason>`."""
    from trading_system.persistence.connection import DatabaseError

    # Inject a clock that always returns a far-future timestamp so the
    # row is "expired" relative to its recorded_at.
    future_clock = lambda: datetime(2099, 1, 1, tzinfo=UTC)  # noqa: E731
    store = SqliteIdempotencyStore(conn=conn, ttl_seconds=60, now=future_clock)
    # Insert a baseline entry while the clock is "real".
    real_store = SqliteIdempotencyStore(conn=conn, ttl_seconds=60)
    real_store.record(
        account_id=AccountId("default"), key="k", body="b", status_code=200
    )
    # Now make the DELETE fail.
    _install(
        conn,
        monkeypatch,
        when=lambda sql: sql.lstrip().upper().startswith("DELETE"),
        exc=DatabaseError("disk failure"),
    )
    match store.lookup(account_id=AccountId("default"), key="k"):
        case Err(reason):
            assert reason.startswith(
                "persistence:corrupt:idempotency_entries:expired_delete:"
            )
        case _:
            raise AssertionError("expected Err")


def test_sweep_expired_database_error_surfaces_err(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    store = SqliteIdempotencyStore(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: sql.lstrip().upper().startswith("DELETE"),
        exc=DatabaseError("sweep failed"),
    )
    match store.sweep_expired(account_id=AccountId("default")):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:idempotency_entries:sweep:")
        case _:
            raise AssertionError("expected Err")
