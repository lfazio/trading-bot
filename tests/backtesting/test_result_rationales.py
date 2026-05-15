"""Tests for the ``rationales`` extension on ``BacktestResult``
(CR-015).

Covers TC_RAT_003 (default empty) + TC_RAT_004 (aligned-length
invariant) + TC_RAT_010 (CR-008 persistence round-trip).

REQ refs: REQ_F_RAT_004, REQ_NF_PER_001, REQ_SDD_PER_006,
REQ_SDD_RAT_003.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.backtesting.result import BacktestResult
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import (
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.money import Currency, Money
from trading_system.models.rationale import TradeRationale
from trading_system.models.trading import Trade
from trading_system.persistence.connection import Connection
from trading_system.persistence.mappers import (
    backtest_result_from_json,
    backtest_result_to_json,
)
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.backtest import (
    BacktestResultRepository,
)
from trading_system.result import Ok

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


def _money(amount: str) -> Money:
    return Money(Decimal(amount), Currency.EUR)


def _trade(day: int) -> Trade:
    return Trade(
        id=TradeId(f"trade-{day}"),
        order_id=OrderId(f"order-{day}"),
        executed_at=datetime(2026, 5, day, 9, 30, tzinfo=UTC),
        price=Decimal("100.0"),
        quantity_filled=Decimal("10"),
        fees=_money("0.50"),
    )


def _point(day: int) -> EquityPoint:
    return EquityPoint(
        at=datetime(2026, 5, day, tzinfo=UTC),
        equity_gross=_money("10000"),
        equity_after_tax=_money("9700"),
        drawdown_pct=Decimal("0.05"),
    )


def _rationale(day: int) -> TradeRationale:
    return TradeRationale(
        trade_id=TradeId(f"trade-{day}"),
        strategy_id=StrategyId("strat-1"),
        strategy_version="sha-abc",
        signal_reason=f"signal day {day}",
        risk_approval={"tax_gate": "pass", "stop_loss": "pass"},
        tax_gate_decision="net > 5*fees",
        improvement_report_id="imp-1",
        decided_at=datetime(2026, 5, day, 9, 30, tzinfo=UTC),
    )


def _result(
    *,
    trades: tuple[Trade, ...] = (),
    rationales: tuple[TradeRationale, ...] = (),
) -> BacktestResult:
    return BacktestResult(
        trades=trades,
        equity_curve=(_point(8),),
        equity_excl_injections=(Decimal("9700"),),
        final_cash=_money("9700"),
        final_equity_after_tax=_money("9700"),
        realized_gross=_money("0"),
        realized_after_tax=_money("0"),
        dividends_gross=_money("0"),
        dividends_after_tax=_money("0"),
        knockouts=0,
        injections_applied=0,
        rationales=rationales,
    )


# ---------------------------------------------------------------------------
# TC_RAT_003 — default empty tuple
# ---------------------------------------------------------------------------


def test_default_rationales_is_empty_tuple() -> None:
    # Construct WITHOUT the ``rationales`` kwarg — existing
    # constructors must keep working.
    result = BacktestResult(
        trades=(_trade(8),),
        equity_curve=(_point(8),),
        equity_excl_injections=(Decimal("9700"),),
        final_cash=_money("9700"),
        final_equity_after_tax=_money("9700"),
        realized_gross=_money("0"),
        realized_after_tax=_money("0"),
        dividends_gross=_money("0"),
        dividends_after_tax=_money("0"),
        knockouts=0,
        injections_applied=0,
    )
    assert result.rationales == ()


def test_empty_rationales_with_non_empty_trades_accepted() -> None:
    # Backwards-compat path: trades present, rationales empty.
    result = _result(trades=(_trade(8),), rationales=())
    assert result.trades == (_trade(8),)
    assert result.rationales == ()


# ---------------------------------------------------------------------------
# TC_RAT_004 — Aligned-length invariant
# ---------------------------------------------------------------------------


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="rationales length must match trades"):
        _result(
            trades=(_trade(8), _trade(9)),
            rationales=(_rationale(8),),
        )


def test_aligned_lengths_accepted() -> None:
    result = _result(
        trades=(_trade(8), _trade(9)),
        rationales=(_rationale(8), _rationale(9)),
    )
    assert len(result.trades) == len(result.rationales)


# ---------------------------------------------------------------------------
# TC_RAT_010 — CR-008 persistence round-trip with rationales
# ---------------------------------------------------------------------------


def test_backtest_result_json_round_trip_preserves_rationales(tmp_path: Path) -> None:
    """REQ_NF_PER_001 / TC_PER_007 family — the BacktestResult mapper
    SHALL round-trip rationales bit-identically."""
    result = _result(
        trades=(_trade(8), _trade(9)),
        rationales=(_rationale(8), _rationale(9)),
    )
    body = backtest_result_to_json(result)
    loaded = backtest_result_from_json(body)
    assert loaded == result
    assert loaded.rationales == result.rationales


def test_empty_rationales_round_trip_stays_empty(tmp_path: Path) -> None:
    result = _result(trades=(), rationales=())
    body = backtest_result_to_json(result)
    loaded = backtest_result_from_json(body)
    assert loaded.rationales == ()


def test_backtest_result_repository_archive_lookup_with_rationales(
    tmp_path: Path,
) -> None:
    """TC_RAT_010 — full round-trip through
    BacktestResultRepository.archive → lookup with rationales
    present. Verifies CR-015 doesn't break TC_PER_007."""
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run()
    repo = BacktestResultRepository(conn=conn)
    result = _result(
        trades=(_trade(8), _trade(9)),
        rationales=(_rationale(8), _rationale(9)),
    )
    assert isinstance(
        repo.archive(
            result,
            strategy_id=StrategyId("strat-1"),
            git_sha="sha-abc",
            config_hash="cfg-1",
            seed=42,
        ),
        Ok,
    )
    loaded = repo.lookup(
        StrategyId("strat-1"),
        "sha-abc",
        "cfg-1",
        42,
    ).unwrap()
    assert loaded == result
    # Spot-check rationales survived bit-identical.
    assert loaded.rationales[0].signal_reason == "signal day 8"
    assert loaded.rationales[1].trade_id == TradeId("trade-9")
