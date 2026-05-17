"""Tests for ``InMemoryIdempotencyStore`` + the
``IdempotencyStore`` Protocol (REQ_F_WEB_008)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.webui.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
)


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _store(ttl: int = 86_400, *, now: datetime = _NOW) -> InMemoryIdempotencyStore:
    return InMemoryIdempotencyStore(ttl_seconds=ttl, now=lambda: now)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_in_memory_store_satisfies_protocol() -> None:
    assert isinstance(_store(), IdempotencyStore)


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


def test_lookup_returns_nothing_for_unknown_key() -> None:
    s = _store()
    match s.lookup(account_id=AccountId("alpha"), key="abc"):
        case Ok(Nothing()):
            pass
        case _:
            raise AssertionError("expected Ok(Nothing)")


def test_lookup_returns_recorded_body() -> None:
    s = _store()
    s.record(
        account_id=AccountId("alpha"), key="abc", body='{"x":1}', status_code=200
    ).unwrap()
    match s.lookup(account_id=AccountId("alpha"), key="abc"):
        case Ok(Some(body)):
            assert body == '{"x":1}'
        case _:
            raise AssertionError("expected Ok(Some(...))")


def test_lookup_returns_nothing_after_ttl() -> None:
    """Lazy TTL — once the clock advances past ttl, ``lookup``
    SHALL return ``Nothing()`` and evict the entry."""
    base = _NOW
    later = base + timedelta(seconds=200)
    clock = {"t": base}
    s = InMemoryIdempotencyStore(ttl_seconds=100, now=lambda: clock["t"])
    s.record(
        account_id=AccountId("alpha"), key="abc", body='{"x":1}', status_code=200
    ).unwrap()
    # Fresh lookup ⇒ Some.
    match s.lookup(account_id=AccountId("alpha"), key="abc"):
        case Ok(Some(_)):
            pass
        case _:
            raise AssertionError("expected Some")
    # Advance the clock past the TTL.
    clock["t"] = later
    match s.lookup(account_id=AccountId("alpha"), key="abc"):
        case Ok(Nothing()):
            pass
        case _:
            raise AssertionError("expected Nothing after ttl")


def test_lookup_isolates_across_accounts() -> None:
    """Two accounts using the same idempotency key SHALL NOT
    collide."""
    s = _store()
    s.record(
        account_id=AccountId("alpha"), key="abc", body="alpha-body", status_code=200
    ).unwrap()
    s.record(
        account_id=AccountId("beta"), key="abc", body="beta-body", status_code=200
    ).unwrap()
    a = s.lookup(account_id=AccountId("alpha"), key="abc").unwrap()
    b = s.lookup(account_id=AccountId("beta"), key="abc").unwrap()
    match a:
        case Some(body):
            assert body == "alpha-body"
        case _:
            raise AssertionError
    match b:
        case Some(body):
            assert body == "beta-body"
        case _:
            raise AssertionError


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def test_record_rejects_empty_key() -> None:
    s = _store()
    match s.record(
        account_id=AccountId("alpha"), key="   ", body="{}", status_code=200
    ):
        case Err(reason):
            assert reason == "webui:idempotency_bad_key"
        case _:
            raise AssertionError("expected Err")


def test_record_same_key_same_body_replays_cleanly() -> None:
    """REQ_F_WEB_008 — replay with identical body SHALL succeed
    (the route layer recorded it again after lookup hit)."""
    s = _store()
    assert isinstance(
        s.record(
            account_id=AccountId("alpha"),
            key="abc",
            body="{}",
            status_code=200,
        ),
        Ok,
    )
    assert isinstance(
        s.record(
            account_id=AccountId("alpha"),
            key="abc",
            body="{}",
            status_code=200,
        ),
        Ok,
    )


def test_record_conflict_for_same_key_different_body() -> None:
    """A divergent body for a known key is a programmer error."""
    s = _store()
    s.record(
        account_id=AccountId("alpha"), key="abc", body='{"x":1}', status_code=200
    ).unwrap()
    match s.record(
        account_id=AccountId("alpha"), key="abc", body='{"x":2}', status_code=200
    ):
        case Err(reason):
            assert reason == "webui:idempotency_conflict"
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# status_code_for
# ---------------------------------------------------------------------------


def test_status_code_for_recorded_entry() -> None:
    s = _store()
    s.record(
        account_id=AccountId("alpha"), key="abc", body="{}", status_code=201
    ).unwrap()
    assert s.status_code_for(account_id=AccountId("alpha"), key="abc") == 201


def test_status_code_for_unknown_entry() -> None:
    s = _store()
    assert s.status_code_for(account_id=AccountId("alpha"), key="abc") is None


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_rejects_zero_ttl() -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        InMemoryIdempotencyStore(ttl_seconds=0)
