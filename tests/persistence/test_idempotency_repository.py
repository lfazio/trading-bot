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
