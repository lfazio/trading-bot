"""Tests for the per-strategy attribution + NAV roll-up.

Pure-function unit tests on ``attribution_from_result``. No I/O,
no broker — fixture ``BacktestResult`` instances drive the
computation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.analytics.attribution import (
    AttributionReport,
    StrategyAttribution,
    attribution_from_result,
)
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


_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def _trade(*, tid: str, oid: str, price: str, qty: str, fees: str = "0.10") -> Trade:
    return Trade(
        id=TradeId(tid),
        order_id=OrderId(oid),
        executed_at=_NOW,
        price=Decimal(price),
        quantity_filled=Decimal(qty),
        fees=Money(Decimal(fees), Currency.EUR),
    )


def _rationale(*, tid: str, strategy: str) -> TradeRationale:
    return TradeRationale(
        trade_id=TradeId(tid),
        strategy_id=StrategyId(strategy),
        strategy_version="v1",
        signal_reason="...",
        risk_approval={},
        tax_gate_decision="ok",
        improvement_report_id="",
        decided_at=_NOW,
    )


def _equity_point(*, at: datetime, amount: str = "10000") -> EquityPoint:
    return EquityPoint(
        at=at,
        equity_gross=Money(Decimal(amount), Currency.EUR),
        equity_after_tax=Money(Decimal(amount), Currency.EUR),
        drawdown_pct=Decimal("0"),
    )


def _result(
    *,
    trades: tuple[Trade, ...],
    rationales: tuple[TradeRationale, ...] = (),
    realized_after_tax: str = "100",
) -> BacktestResult:
    return BacktestResult(
        trades=trades,
        equity_curve=(_equity_point(at=_NOW),),
        equity_excl_injections=(Decimal("10000"),),
        final_cash=Money(Decimal("10000"), Currency.EUR),
        final_equity_after_tax=Money(Decimal("10000"), Currency.EUR),
        realized_gross=Money(Decimal("100"), Currency.EUR),
        realized_after_tax=Money(Decimal(realized_after_tax), Currency.EUR),
        dividends_gross=Money(Decimal("0"), Currency.EUR),
        dividends_after_tax=Money(Decimal("0"), Currency.EUR),
        knockouts=0,
        injections_applied=0,
        rationales=rationales,
    )


# ---------------------------------------------------------------------------
# Empty result -> empty table, zero portfolio totals
# ---------------------------------------------------------------------------


def test_attribution_empty_result_returns_zero_totals() -> None:
    report = attribution_from_result(_result(trades=(), rationales=()))
    assert isinstance(report, AttributionReport)
    assert report.by_strategy == ()
    assert report.portfolio_trade_count == 0
    assert report.portfolio_turnover.amount == Decimal("0")
    assert report.portfolio_fees.amount == Decimal("0")
    assert report.currency == Currency.EUR


# ---------------------------------------------------------------------------
# Single-strategy run -> 100% turnover share + full realized PnL
# ---------------------------------------------------------------------------


def test_attribution_single_strategy_carries_full_share() -> None:
    trades = (
        _trade(tid="t-1", oid="o-1", price="100", qty="5", fees="1.00"),
        _trade(tid="t-2", oid="o-2", price="105", qty="5", fees="1.00"),
    )
    rationales = (
        _rationale(tid="t-1", strategy="CoreStrategy"),
        _rationale(tid="t-2", strategy="CoreStrategy"),
    )
    report = attribution_from_result(
        _result(trades=trades, rationales=rationales, realized_after_tax="50")
    )
    assert report.portfolio_trade_count == 2
    # Turnover = 100*5 + 105*5 = 500 + 525 = 1025
    assert report.portfolio_turnover.amount == Decimal("1025")
    assert report.portfolio_fees.amount == Decimal("2.00")
    assert len(report.by_strategy) == 1
    row: StrategyAttribution = report.by_strategy[0]
    assert row.strategy_id == "CoreStrategy"
    assert row.trade_count == 2
    assert row.total_turnover.amount == Decimal("1025")
    assert row.total_fees.amount == Decimal("2.00")
    assert row.turnover_share_pct == Decimal("100.00")
    # Full realized PnL goes to the only strategy.
    assert row.realized_pnl_proxy.amount == Decimal("50.0000")


# ---------------------------------------------------------------------------
# Two-strategy split -> proportional shares
# ---------------------------------------------------------------------------


def test_attribution_two_strategies_split_realized_pnl_proportionally() -> None:
    trades = (
        # CoreStrategy: turnover 100*10 = 1000
        _trade(tid="t-1", oid="o-1", price="100", qty="10", fees="2.00"),
        # TacticalStrategy: turnover 100*5 = 500 + 50*5 = 250 => 750
        _trade(tid="t-2", oid="o-2", price="100", qty="5", fees="1.00"),
        _trade(tid="t-3", oid="o-3", price="50", qty="5", fees="0.50"),
    )
    rationales = (
        _rationale(tid="t-1", strategy="CoreStrategy"),
        _rationale(tid="t-2", strategy="TacticalStrategy"),
        _rationale(tid="t-3", strategy="TacticalStrategy"),
    )
    report = attribution_from_result(
        _result(trades=trades, rationales=rationales, realized_after_tax="350")
    )
    # Portfolio turnover = 1000 + 750 = 1750
    assert report.portfolio_turnover.amount == Decimal("1750")
    # Two rows, sorted by strategy_id alphabetically.
    assert len(report.by_strategy) == 2
    [core, tactical] = report.by_strategy
    assert core.strategy_id == "CoreStrategy"
    assert tactical.strategy_id == "TacticalStrategy"
    # CoreStrategy share = 1000 / 1750 ≈ 57.14%
    assert core.turnover_share_pct == Decimal("57.14")
    # TacticalStrategy share = 750 / 1750 ≈ 42.86%
    assert tactical.turnover_share_pct == Decimal("42.86")
    # PnL split: 350 * 1000/1750 ≈ 200; 350 * 750/1750 = 150
    assert core.realized_pnl_proxy.amount == Decimal("200.0000")
    assert tactical.realized_pnl_proxy.amount == Decimal("150.0000")
    # Trade counts.
    assert core.trade_count == 1
    assert tactical.trade_count == 2
    # Fees flow through unchanged.
    assert core.total_fees.amount == Decimal("2.00")
    assert tactical.total_fees.amount == Decimal("1.50")


# ---------------------------------------------------------------------------
# Trades without a matching rationale land under "unknown"
# ---------------------------------------------------------------------------


def test_attribution_orphan_trades_land_under_unknown_bucket() -> None:
    """Legacy runs without TradeRationale rows SHALL produce a
    valid report with the orphan trades under "unknown"."""
    trades = (
        _trade(tid="t-1", oid="o-1", price="100", qty="5"),
        _trade(tid="t-2", oid="o-2", price="100", qty="5"),
    )
    # No rationales attached.
    report = attribution_from_result(_result(trades=trades))
    assert len(report.by_strategy) == 1
    row = report.by_strategy[0]
    assert row.strategy_id == "unknown"
    assert row.trade_count == 2


# ---------------------------------------------------------------------------
# Determinism — same input -> same output
# ---------------------------------------------------------------------------


def test_attribution_is_deterministic() -> None:
    """Same BacktestResult ⇒ same AttributionReport (REQ_NF_REP_001
    extension: pure-function determinism)."""
    trades = (
        _trade(tid="t-1", oid="o-1", price="100", qty="5", fees="1.00"),
        _trade(tid="t-2", oid="o-2", price="100", qty="5", fees="1.00"),
    )
    rationales = (
        _rationale(tid="t-1", strategy="CoreStrategy"),
        _rationale(tid="t-2", strategy="TacticalStrategy"),
    )
    result = _result(trades=trades, rationales=rationales)
    assert attribution_from_result(result) == attribution_from_result(result)


def test_attribution_rows_sorted_alphabetically() -> None:
    """Iteration order SHALL be deterministic: strategy_id ASC."""
    trades = tuple(
        _trade(tid=f"t-{i}", oid=f"o-{i}", price="100", qty="1")
        for i in range(3)
    )
    rationales = (
        _rationale(tid="t-0", strategy="Zeta"),
        _rationale(tid="t-1", strategy="Alpha"),
        _rationale(tid="t-2", strategy="Mu"),
    )
    report = attribution_from_result(
        _result(trades=trades, rationales=rationales)
    )
    assert [r.strategy_id for r in report.by_strategy] == ["Alpha", "Mu", "Zeta"]
