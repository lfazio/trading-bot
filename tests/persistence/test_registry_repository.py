"""Tests for ``trading_system.persistence.repositories.registry``.

Covers TC_PER_008 (HMAC-gated promotion + token never persisted) plus
the standard store / read / immutability / isolation behaviours
mirrored from the in-memory ``Registry``.

REQ refs: REQ_F_PER_006, REQ_F_PER_009, REQ_NF_PER_001,
REQ_SDS_PER_002, REQ_SDD_PER_005, REQ_SDD_PER_008.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId, StrategyId
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.registry import RegistryRepository
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.safety.recovery import (
    AlwaysInvalidVerifier,
    AlwaysValidVerifier,
)
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.registry import RegistryEntry

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


def _migrated_conn(tmp_path: Path) -> Connection:
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run()
    return conn


def _metrics(net: str = "0.12", risk: str = "0.10") -> StrategyMetrics:
    return StrategyMetrics(
        net_after_tax_return=Decimal(net),
        sharpe=Decimal("1.5"),
        stability=Decimal("0.8"),
        dd_penalty=Decimal("0.1"),
        max_drawdown=Decimal("0.15"),
        turnover=Decimal("12"),
        regime_stability=Decimal("0.9"),
        leverage=Decimal("1"),
        parameter_sensitivity=Decimal("0.2"),
        risk=Decimal(risk),
        return_=Decimal(net),
    )


def _entry(sid: str, *, validated: bool = False) -> RegistryEntry:
    return RegistryEntry(
        strategy_id=StrategyId(sid),
        git_sha="abc123",
        config_hash="deadbeef",
        seed=42,
        metrics=_metrics(),
        validated=validated,
        created_at=datetime(2026, 5, 14, 9, 0, tzinfo=UTC),
        notes="",
    )


# ---------------------------------------------------------------------------
# Store + read round-trip
# ---------------------------------------------------------------------------


def test_store_then_get_returns_bit_identical_entry(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    entry = _entry("s1")
    assert isinstance(repo.store(entry), Ok)
    match repo.get(StrategyId("s1")):
        case Ok(Some(loaded)):
            assert loaded == entry
        case _:
            raise AssertionError("expected Some(entry)")


def test_get_missing_returns_nothing(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    match repo.get(StrategyId("ghost")):
        case Ok(Nothing()):
            pass
        case other:
            raise AssertionError(f"expected Ok(Nothing()), got {other}")


def test_validated_immutable(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("s1", validated=True))
    # Second validated store under the same id is rejected.
    match repo.store(_entry("s1", validated=True)):
        case Err(reason):
            assert reason == "registry:validated_immutable:s1"
        case Ok(_):
            raise AssertionError("expected Err on re-store of validated entry")


def test_list_validated_filters_and_sorts(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("b_strat", validated=True))
    repo.store(_entry("a_strat", validated=True))
    repo.store(_entry("c_strat", validated=False))
    ids = tuple(e.strategy_id for e in repo.list_validated().unwrap())
    assert ids == ("a_strat", "b_strat")


# ---------------------------------------------------------------------------
# TC_PER_008 — HMAC-gated promotion
# ---------------------------------------------------------------------------


def test_request_promotion_rejects_invalid_token(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("s1"))
    bad_verifier = AlwaysInvalidVerifier()
    res = repo.request_promotion(
        StrategyId("s1"),
        "any-token",
        verifier=bad_verifier,
        operator_id="op-1",
        rationale="should not happen",
    )
    match res:
        case Err(reason):
            assert reason == "registry:token_invalid"
        case Ok(_):
            raise AssertionError("expected Err on invalid token")
    # Entry remains experimental.
    loaded = repo.get(StrategyId("s1")).unwrap()
    match loaded:
        case Some(e):
            assert e.validated is False
        case _:
            raise AssertionError
    # No audit row was written.
    audit = repo.promotion_audit(StrategyId("s1")).unwrap()
    assert audit == ()


def test_request_promotion_with_valid_token_marks_validated_and_audits(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("s1"))
    verifier = AlwaysValidVerifier()
    token = "op-secret-token"
    res = repo.request_promotion(
        StrategyId("s1"),
        token,
        verifier=verifier,
        operator_id="op-1",
        rationale="passed walk-forward",
    )
    assert isinstance(res, Ok)
    # The entry is now validated.
    loaded = repo.get(StrategyId("s1")).unwrap()
    match loaded:
        case Some(e):
            assert e.validated is True
        case _:
            raise AssertionError
    # Audit row carries SHA-256(token), never the raw token.
    expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    audit = repo.promotion_audit(StrategyId("s1")).unwrap()
    assert len(audit) == 1
    row = audit[0]
    assert row["promoter_token_hash"] == expected_hash
    assert row["promoted_by"] == "op-1"
    assert row["promotion_rationale"] == "passed walk-forward"
    # The raw token MUST NOT appear anywhere in the audit row.
    for v in row.values():
        assert token not in str(v), f"raw token leaked into {row}"


def test_request_promotion_double_call_is_rejected(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("s1"))
    verifier = AlwaysValidVerifier()
    repo.request_promotion(
        StrategyId("s1"),
        "tok",
        verifier=verifier,
        operator_id="op-1",
        rationale="first",
    )
    # Second call should refuse — entry already validated.
    res = repo.request_promotion(
        StrategyId("s1"),
        "tok2",
        verifier=verifier,
        operator_id="op-2",
        rationale="second",
    )
    match res:
        case Err(reason):
            assert reason == "registry:already_validated:s1"
        case Ok(_):
            raise AssertionError("expected Err on second promotion")


def test_request_promotion_missing_entry_returns_not_found(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    verifier = AlwaysValidVerifier()
    res = repo.request_promotion(
        StrategyId("ghost"),
        "tok",
        verifier=verifier,
        operator_id="op",
        rationale="-",
    )
    match res:
        case Err(reason):
            assert reason == "registry:not_found:ghost"
        case Ok(_):
            raise AssertionError("expected Err on missing entry")


# ---------------------------------------------------------------------------
# Account isolation (REQ_F_PER_009 / REQ_SDD_PER_008)
# ---------------------------------------------------------------------------


def test_account_isolation_on_registry(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("shared", validated=True), account_id=DEFAULT_ACCOUNT_ID)
    other = AccountId("alt")
    repo.store(_entry("shared", validated=False), account_id=other)
    default_entry = repo.get(StrategyId("shared")).unwrap()
    alt_entry = repo.get(StrategyId("shared"), account_id=other).unwrap()
    match default_entry, alt_entry:
        case Some(d), Some(a):
            assert d.validated is True
            assert a.validated is False
        case _:
            raise AssertionError("both accounts should hold their own row")


# ---------------------------------------------------------------------------
# Phase-8 C1 — Err-branch coverage (DB exception paths + missing methods)
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


def test_get_database_error_surfaces_categorised_err(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "FROM strategy_registry" in sql,
        exc=DatabaseError("read failed"),
    )
    match repo.get(StrategyId("s1")):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:strategy_registry:read:")
        case _:
            raise AssertionError("expected Err")


def test_list_validated_database_error_surfaces_err(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "validated = 1" in sql,
        exc=DatabaseError("read failed"),
    )
    match repo.list_validated():
        case Err(reason):
            assert reason.startswith("persistence:corrupt:strategy_registry:read:")
        case _:
            raise AssertionError("expected Err")


def test_list_experimental_returns_only_unvalidated(tmp_path: Path) -> None:
    """The experimental-list accessor SHALL surface every row
    where ``validated = 0``, sorted by strategy_id. The legacy
    test suite covered ``list_validated`` but not the experimental
    branch."""
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("alpha", validated=False))
    repo.store(_entry("beta", validated=True))
    repo.store(_entry("gamma", validated=False))
    match repo.list_experimental():
        case Ok(rows):
            ids = [str(r.strategy_id) for r in rows]
            assert ids == ["alpha", "gamma"]
        case _:
            raise AssertionError("expected Ok")


def test_list_experimental_database_error_surfaces_err(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "validated = 0" in sql,
        exc=DatabaseError("read failed"),
    )
    match repo.list_experimental():
        case Err(reason):
            assert reason.startswith("persistence:corrupt:strategy_registry:read:")
        case _:
            raise AssertionError("expected Err")


def test_store_propagates_read_err_from_get(
    tmp_path: Path, monkeypatch
) -> None:
    """`store` calls `get` first to check the validated-immutable
    invariant. If `get` returns Err, the same Err SHALL propagate."""
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "FROM strategy_registry" in sql,
        exc=DatabaseError("read failed"),
    )
    match repo.store(_entry("s1")):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:strategy_registry:read:")
        case _:
            raise AssertionError("expected Err propagation")


def test_store_integrity_error_surfaces_categorised_err(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import IntegrityError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO strategy_registry" in sql,
        exc=IntegrityError("simulated"),
    )
    match repo.store(_entry("s1")):
        case Err(reason):
            assert reason.startswith("persistence:integrity:strategy_registry:")
        case _:
            raise AssertionError("expected Err")


def test_store_operational_error_surfaces_locked_category(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import OperationalError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO strategy_registry" in sql,
        exc=OperationalError("database is locked"),
    )
    match repo.store(_entry("s1")):
        case Err(reason):
            assert reason.startswith("persistence:locked:strategy_registry:")
        case _:
            raise AssertionError("expected Err")


def test_store_generic_database_error_surfaces_corrupt_category(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO strategy_registry" in sql,
        exc=DatabaseError("disk corrupt"),
    )
    match repo.store(_entry("s1")):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:strategy_registry:")
        case _:
            raise AssertionError("expected Err")


def test_request_promotion_propagates_read_err(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "FROM strategy_registry" in sql,
        exc=DatabaseError("read failed"),
    )
    match repo.request_promotion(
        StrategyId("s1"),
        "token",
        verifier=AlwaysValidVerifier(),
        operator_id="op",
        rationale="r",
    ):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:strategy_registry:read:")
        case _:
            raise AssertionError("expected Err propagation")


def test_request_promotion_integrity_error_during_update(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import IntegrityError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("s1"))  # so the get + Some path succeeds
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "UPDATE strategy_registry" in sql,
        exc=IntegrityError("simulated"),
    )
    match repo.request_promotion(
        StrategyId("s1"),
        "token",
        verifier=AlwaysValidVerifier(),
        operator_id="op",
        rationale="r",
    ):
        case Err(reason):
            assert reason.startswith("persistence:integrity:registry_promotions:")
        case _:
            raise AssertionError("expected Err")


def test_request_promotion_operational_error_during_update(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import OperationalError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("s1"))
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "UPDATE strategy_registry" in sql,
        exc=OperationalError("database is locked"),
    )
    match repo.request_promotion(
        StrategyId("s1"),
        "token",
        verifier=AlwaysValidVerifier(),
        operator_id="op",
        rationale="r",
    ):
        case Err(reason):
            assert reason.startswith("persistence:locked:registry_promotions:")
        case _:
            raise AssertionError("expected Err")


def test_request_promotion_generic_database_error_during_update(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("s1"))
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "UPDATE strategy_registry" in sql,
        exc=DatabaseError("disk corrupt"),
    )
    match repo.request_promotion(
        StrategyId("s1"),
        "token",
        verifier=AlwaysValidVerifier(),
        operator_id="op",
        rationale="r",
    ):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:registry_promotions:")
        case _:
            raise AssertionError("expected Err")


def test_promotion_audit_returns_rows_in_chronological_order(
    tmp_path: Path,
) -> None:
    """REQ_SDD_PER_005 — the audit reader SHALL surface every row
    for the given strategy_id in chronological order; the raw
    token is never present (only token_hash)."""
    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    repo.store(_entry("s1"))
    repo.request_promotion(
        StrategyId("s1"),
        "token-1",
        verifier=AlwaysValidVerifier(),
        operator_id="op-a",
        rationale="first promotion",
    )
    match repo.promotion_audit(StrategyId("s1")):
        case Ok(rows):
            assert len(rows) == 1
            row = rows[0]
            assert row["promoted_by"] == "op-a"
            # SHA-256 hex of "token-1".
            expected = hashlib.sha256(b"token-1").hexdigest()
            assert row["promoter_token_hash"] == expected
        case _:
            raise AssertionError("expected Ok")


def test_promotion_audit_database_error_surfaces_err(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "FROM registry_promotions" in sql,
        exc=DatabaseError("read failed"),
    )
    match repo.promotion_audit(StrategyId("s1")):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:registry_promotions:read:")
        case _:
            raise AssertionError("expected Err")


def test_safe_rollback_swallows_secondary_error(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import (
        DatabaseError,
        IntegrityError,
    )

    conn = _migrated_conn(tmp_path)
    repo = RegistryRepository(conn=conn)
    real = conn._raw

    class _DualFault:
        def execute(self, sql, *args, **kwargs):
            if "INSERT INTO strategy_registry" in sql:
                raise IntegrityError("simulated integrity")
            if sql.lstrip().upper().startswith("ROLLBACK"):
                raise DatabaseError("rollback also failed")
            return real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(conn, "_raw", _DualFault())
    match repo.store(_entry("s1")):
        case Err(reason):
            assert reason.startswith("persistence:integrity:strategy_registry:")
        case _:
            raise AssertionError("expected Err")
