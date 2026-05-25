"""Tests for ``trading_system.persistence.repositories.backtest``.

Covers TC_PER_007 (archive → lookup round-trip is bit-identical on
the exact replay tuple) plus the not-found path.

REQ refs: REQ_F_PER_007, REQ_NF_PER_001, REQ_NF_REP_001,
REQ_SDD_PER_006.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trading_system.backtesting.result import BacktestResult
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import (
    DEFAULT_ACCOUNT_ID,
    AccountId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Trade
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.backtest import BacktestResultRepository
from trading_system.result import Err, Ok

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


def _migrated_conn(tmp_path: Path) -> Connection:
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run()
    return conn


def _money(x: str) -> Money:
    return Money(Decimal(x), Currency.EUR)


def _trade(day: int, price: str = "100.5", qty: str = "10") -> Trade:
    return Trade(
        id=TradeId(f"t-{day}"),
        order_id=OrderId(f"o-{day}"),
        executed_at=datetime(2026, 5, day, 10, 0, tzinfo=UTC),
        price=Decimal(price),
        quantity_filled=Decimal(qty),
        fees=_money("0.95"),
        slippage=Decimal("0.0001"),
    )


def _point(day: int) -> EquityPoint:
    return EquityPoint(
        at=datetime(2026, 5, day, tzinfo=UTC),
        equity_gross=_money(f"{10000 + day}.123"),
        equity_after_tax=_money(f"{7000 + day}.456"),
        drawdown_pct=Decimal("0.01"),
    )


def _result() -> BacktestResult:
    return BacktestResult(
        trades=(_trade(8), _trade(9)),
        equity_curve=(_point(8), _point(9)),
        equity_excl_injections=(Decimal("10000.1"), Decimal("10001.2")),
        final_cash=_money("500.50"),
        final_equity_after_tax=_money("10001.23456789"),
        realized_gross=_money("1234.56789"),
        realized_after_tax=_money("864.20"),
        dividends_gross=_money("12.34"),
        dividends_after_tax=_money("8.638"),
        knockouts=1,
        injections_applied=2,
    )


# ---------------------------------------------------------------------------
# TC_PER_007 — archive → lookup round-trip
# ---------------------------------------------------------------------------


def test_archive_then_lookup_round_trip(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = BacktestResultRepository(conn=conn)
    result = _result()
    assert isinstance(
        repo.archive(
            result,
            strategy_id=StrategyId("alpha"),
            git_sha="sha1",
            config_hash="cfg1",
            seed=7,
        ),
        Ok,
    )
    loaded = repo.lookup(
        StrategyId("alpha"),
        "sha1",
        "cfg1",
        7,
    ).unwrap()
    # Bit-identical structural equality (REQ_NF_PER_001).
    assert loaded == result


def test_lookup_with_wrong_tuple_returns_not_found(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = BacktestResultRepository(conn=conn)
    repo.archive(
        _result(),
        strategy_id=StrategyId("alpha"),
        git_sha="sha1",
        config_hash="cfg1",
        seed=7,
    )
    # Wrong seed.
    match repo.lookup(StrategyId("alpha"), "sha1", "cfg1", 999):
        case Err(reason):
            assert reason.startswith("persistence:not_found:backtest_results:")
        case Ok(_):
            raise AssertionError("expected not_found")
    # Wrong sha.
    match repo.lookup(StrategyId("alpha"), "sha-other", "cfg1", 7):
        case Err(reason):
            assert reason.startswith("persistence:not_found:backtest_results:")
        case Ok(_):
            raise AssertionError("expected not_found")


def test_re_archive_overwrites_same_key(tmp_path: Path) -> None:
    """Replaying with the same key replaces the prior archive; the
    result is still bit-identical with the most recent write."""
    conn = _migrated_conn(tmp_path)
    repo = BacktestResultRepository(conn=conn)
    repo.archive(
        _result(),
        strategy_id=StrategyId("alpha"),
        git_sha="sha1",
        config_hash="cfg1",
        seed=7,
    )
    # Build a different result with the same key.
    altered = BacktestResult(
        trades=(_trade(10),),
        equity_curve=(_point(10),),
        equity_excl_injections=(Decimal("20000"),),
        final_cash=_money("1.00"),
        final_equity_after_tax=_money("20000"),
        realized_gross=_money("0"),
        realized_after_tax=_money("0"),
        dividends_gross=_money("0.01"),
        dividends_after_tax=_money("0.007"),
        knockouts=0,
        injections_applied=0,
    )
    repo.archive(
        altered,
        strategy_id=StrategyId("alpha"),
        git_sha="sha1",
        config_hash="cfg1",
        seed=7,
    )
    loaded = repo.lookup(StrategyId("alpha"), "sha1", "cfg1", 7).unwrap()
    assert loaded == altered


def test_account_isolation_on_backtest_archive(tmp_path: Path) -> None:
    conn = _migrated_conn(tmp_path)
    repo = BacktestResultRepository(conn=conn)
    other = AccountId("alt")
    repo.archive(
        _result(),
        strategy_id=StrategyId("alpha"),
        git_sha="sha1",
        config_hash="cfg1",
        seed=7,
        account_id=DEFAULT_ACCOUNT_ID,
    )
    # Same key under a different account — should not collide.
    match repo.lookup(
        StrategyId("alpha"), "sha1", "cfg1", 7, account_id=other
    ):
        case Err(reason):
            assert reason.startswith("persistence:not_found:")
        case Ok(_):
            raise AssertionError("alt account should not see default's archive")


# ---------------------------------------------------------------------------
# Phase-8 C1 — Err-branch coverage (DB exception paths)
# ---------------------------------------------------------------------------


class _RaisingExecProxy:
    """Proxy around ``sqlite3.Connection`` raising ``exc`` on a
    matching SQL. Used to exercise the repository's DatabaseError
    branches without needing a real corrupt DB."""

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


def test_archive_integrity_error_surfaces_categorised_err(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import IntegrityError

    conn = _migrated_conn(tmp_path)
    repo = BacktestResultRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO backtest_results" in sql,
        exc=IntegrityError("UNIQUE constraint failed"),
    )
    match repo.archive(
        _result(),
        strategy_id=StrategyId("alpha"),
        git_sha="sha1",
        config_hash="cfg1",
        seed=7,
    ):
        case Err(reason):
            assert reason.startswith("persistence:integrity:backtest_results:")
        case Ok(_):
            raise AssertionError("expected Err on integrity")


def test_archive_operational_error_surfaces_locked_category(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import OperationalError

    conn = _migrated_conn(tmp_path)
    repo = BacktestResultRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO backtest_results" in sql,
        exc=OperationalError("database is locked"),
    )
    match repo.archive(
        _result(),
        strategy_id=StrategyId("alpha"),
        git_sha="sha1",
        config_hash="cfg1",
        seed=7,
    ):
        case Err(reason):
            assert reason.startswith("persistence:locked:backtest_results:")
        case Ok(_):
            raise AssertionError("expected Err on operational")


def test_archive_generic_database_error_surfaces_corrupt_category(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = BacktestResultRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: "INSERT INTO backtest_results" in sql,
        exc=DatabaseError("disk image corrupt"),
    )
    match repo.archive(
        _result(),
        strategy_id=StrategyId("alpha"),
        git_sha="sha1",
        config_hash="cfg1",
        seed=7,
    ):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:backtest_results:")
        case Ok(_):
            raise AssertionError("expected Err on generic DB")


def test_lookup_database_error_surfaces_categorised_err(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import DatabaseError

    conn = _migrated_conn(tmp_path)
    repo = BacktestResultRepository(conn=conn)
    _install(
        conn,
        monkeypatch,
        when=lambda sql: sql.lstrip().upper().startswith("SELECT"),
        exc=DatabaseError("read failed"),
    )
    match repo.lookup(StrategyId("alpha"), "sha1", "cfg1", 7):
        case Err(reason):
            assert reason.startswith("persistence:corrupt:backtest_results:read:")
        case Ok(_):
            raise AssertionError("expected Err on read failure")


def test_safe_rollback_swallows_secondary_error(
    tmp_path: Path, monkeypatch
) -> None:
    from trading_system.persistence.connection import (
        DatabaseError,
        IntegrityError,
    )

    conn = _migrated_conn(tmp_path)
    repo = BacktestResultRepository(conn=conn)
    real = conn._raw

    class _DualFault:
        def execute(self, sql, *args, **kwargs):
            if "INSERT INTO backtest_results" in sql:
                raise IntegrityError("simulated integrity")
            if sql.lstrip().upper().startswith("ROLLBACK"):
                raise DatabaseError("rollback also failed")
            return real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(conn, "_raw", _DualFault())
    match repo.archive(
        _result(),
        strategy_id=StrategyId("alpha"),
        git_sha="sha1",
        config_hash="cfg1",
        seed=7,
    ):
        case Err(reason):
            assert reason.startswith("persistence:integrity:backtest_results:")
        case Ok(_):
            raise AssertionError("expected Err")
