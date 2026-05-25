"""``TradeApprovalAuditRepository`` tests — CR-001 Phase B
persistence (REQ_F_NOT_004 / REQ_F_NOT_005 / REQ_NF_NOT_003).

The raw operator token NEVER lands in the database; only its
SHA-256 hash is recorded. Schema lives in
``persistence/migrations/0003_approvals.sql``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.models.identifiers import AccountId, InstrumentId
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side
from trading_system.notifications.approval import operator_token_hash
from trading_system.notifications.payloads import (
    ApprovalResponse,
    TradeApprovalRequest,
)
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories import TradeApprovalAuditRepository
from trading_system.result import Err, Nothing, Ok, Some


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


_AT = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
_EXPIRES = datetime(2026, 5, 18, 12, 15, tzinfo=UTC)


def _request(request_id: str = "req-001") -> TradeApprovalRequest:
    return TradeApprovalRequest(
        request_id=request_id,
        account_id=AccountId("default"),
        instrument=InstrumentId("ASML.AS"),
        side=Side.BUY,
        quantity=Decimal("10"),
        expected_loss=Money(Decimal("250.00"), Currency.EUR),
        rationale_digest="yield>4.5; payout<70",
        requested_at=_AT,
        expires_at=_EXPIRES,
    )


def _response(
    *,
    request_id: str = "req-001",
    approved: bool = True,
    token: str = "raw-operator-token",
) -> ApprovalResponse:
    return ApprovalResponse(
        request_id=request_id,
        approved=approved,
        operator_token=token,
        responded_at=datetime(2026, 5, 18, 12, 5, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# record_request happy path + duplicate
# ---------------------------------------------------------------------------


def test_record_and_read_request(conn: Connection) -> None:
    repo = TradeApprovalAuditRepository(conn=conn)
    assert isinstance(repo.record_request(_request()), Ok)
    match repo.get_request("req-001"):
        case Ok(Some(row)):
            assert row["instrument_id"] == "ASML.AS"
            assert row["quantity"] == "10"
            assert row["expected_loss_amount"] == "250.00"
            assert row["rationale_digest"] == "yield>4.5; payout<70"
        case _:
            raise AssertionError("expected Ok(Some(row))")


def test_get_missing_request_returns_nothing(conn: Connection) -> None:
    repo = TradeApprovalAuditRepository(conn=conn)
    match repo.get_request("ghost"):
        case Ok(Nothing()):
            pass
        case _:
            raise AssertionError("expected Ok(Nothing())")


def test_duplicate_request_id_surfaces_integrity_err(conn: Connection) -> None:
    repo = TradeApprovalAuditRepository(conn=conn)
    repo.record_request(_request()).unwrap()
    match repo.record_request(_request()):
        case Err(reason):
            assert reason.startswith(
                "persistence:integrity:approval_requests:duplicate:req-001"
            )
        case _:
            raise AssertionError("expected duplicate Err")


# ---------------------------------------------------------------------------
# Credential safety — REQ_F_NOT_005 / REQ_NF_NOT_003
# ---------------------------------------------------------------------------


def test_response_persists_only_token_hash(conn: Connection) -> None:
    """REQ_NF_NOT_003 — the raw operator token NEVER lands in the
    audit row. Only its SHA-256 hash does."""
    repo = TradeApprovalAuditRepository(conn=conn)
    repo.record_request(_request()).unwrap()
    raw_token = "very-secret-operator-token-do-not-leak"
    repo.record_response(_response(token=raw_token), operator_id="alice").unwrap()
    match repo.get_response("req-001"):
        case Ok(Some(row)):
            assert row["operator_token_hash"] == operator_token_hash(raw_token)
            # Defence-in-depth: scan the entire row's stringified form.
            assert raw_token not in repr(row)
            assert raw_token not in str(row.values())
        case _:
            raise AssertionError("expected Ok(Some(row))")


def test_verify_token_matches_persisted_hash(conn: Connection) -> None:
    """REQ_F_NOT_005 — the audit's verify_token helper matches the
    operator's raw token against the persisted hash without
    materialising the raw token anywhere on disk."""
    repo = TradeApprovalAuditRepository(conn=conn)
    repo.record_request(_request()).unwrap()
    raw_token = "correct-token-abc"
    repo.record_response(_response(token=raw_token), operator_id="alice").unwrap()
    match repo.verify_token("req-001", raw_token):
        case Ok(ok):
            assert ok is True
        case _:
            raise AssertionError("expected Ok(True)")
    match repo.verify_token("req-001", "wrong-token"):
        case Ok(ok):
            assert ok is False
        case _:
            raise AssertionError("expected Ok(False) for wrong token")


def test_verify_token_missing_response_returns_false(conn: Connection) -> None:
    repo = TradeApprovalAuditRepository(conn=conn)
    match repo.verify_token("req-never-existed", "x"):
        case Ok(ok):
            assert ok is False
        case _:
            raise AssertionError("expected Ok(False) for missing response")


# ---------------------------------------------------------------------------
# Response rejection path
# ---------------------------------------------------------------------------


def test_record_denial_carries_rejection_reason(conn: Connection) -> None:
    repo = TradeApprovalAuditRepository(conn=conn)
    repo.record_request(_request()).unwrap()
    repo.record_response(
        _response(approved=False, token="t"),
        operator_id="alice",
        rejection_reason="notifications:approval_denied:req-001",
    ).unwrap()
    match repo.get_response("req-001"):
        case Ok(Some(row)):
            assert row["approved"] == 0
            assert row["rejection_reason"] == "notifications:approval_denied:req-001"
        case _:
            raise AssertionError("expected Ok(Some(row))")


# ---------------------------------------------------------------------------
# Account isolation
# ---------------------------------------------------------------------------


def test_account_id_isolates_request_rows(conn: Connection) -> None:
    repo = TradeApprovalAuditRepository(conn=conn)
    repo.record_request(_request(), account_id=AccountId("alpha")).unwrap()
    repo.record_request(_request(), account_id=AccountId("beta")).unwrap()
    # Same request_id can live under each account without collision.
    match repo.get_request("req-001", account_id=AccountId("alpha")):
        case Ok(Some(row)):
            assert row["account_id"] == "alpha"
        case _:
            raise AssertionError("alpha request missing")
    match repo.get_request("req-001", account_id=AccountId("beta")):
        case Ok(Some(row)):
            assert row["account_id"] == "beta"
        case _:
            raise AssertionError("beta request missing")


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


def test_record_request_generic_database_error_surfaces_err(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    repo = TradeApprovalAuditRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO approval_requests" in sql,
        exc=DatabaseError("disk corrupt"),
    )
    match repo.record_request(_request()):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:approval_requests:write:")
        case _:
            raise AssertionError("expected Err")


def test_record_response_integrity_error_surfaces_categorised_err(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import IntegrityError

    repo = TradeApprovalAuditRepository(conn=conn)
    repo.record_request(_request())  # so the FK target exists
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO approval_responses" in sql,
        exc=IntegrityError("simulated"),
    )
    match repo.record_response(_response(), operator_id="op"):
        case Err(reason):
            assert reason.startswith(
                "persistence:integrity:approval_responses:duplicate_or_missing:"
            )
        case _:
            raise AssertionError("expected Err")


def test_record_response_generic_database_error_surfaces_err(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    repo = TradeApprovalAuditRepository(conn=conn)
    repo.record_request(_request())
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO approval_responses" in sql,
        exc=DatabaseError("disk corrupt"),
    )
    match repo.record_response(_response(), operator_id="op"):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:approval_responses:write:")
        case _:
            raise AssertionError("expected Err")


def test_get_request_database_error_surfaces_err(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    repo = TradeApprovalAuditRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "FROM approval_requests" in sql,
        exc=DatabaseError("read failed"),
    )
    match repo.get_request("req-001"):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:approval_requests:read:")
        case _:
            raise AssertionError("expected Err")


def test_get_response_missing_returns_nothing(conn: Connection) -> None:
    repo = TradeApprovalAuditRepository(conn=conn)
    match repo.get_response("ghost"):
        case Ok(Nothing()):
            pass
        case _:
            raise AssertionError("expected Ok(Nothing())")


def test_get_response_database_error_surfaces_err(
    conn: Connection, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    repo = TradeApprovalAuditRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "FROM approval_responses" in sql,
        exc=DatabaseError("read failed"),
    )
    match repo.get_response("req-001"):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:approval_responses:read:")
        case _:
            raise AssertionError("expected Err")


def test_verify_token_propagates_read_err_from_get_response(
    conn: Connection, monkeypatch
) -> None:
    """`verify_token` calls `get_response` internally; an Err there
    SHALL propagate so the caller sees the persistence failure."""
    from trading_system.persistence.connection import DatabaseError

    repo = TradeApprovalAuditRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "FROM approval_responses" in sql,
        exc=DatabaseError("read failed"),
    )
    match repo.verify_token("req-001", "any-token"):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:approval_responses:read:")
        case _:
            raise AssertionError("expected Err propagation")
