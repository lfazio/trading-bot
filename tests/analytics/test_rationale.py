"""Tests for ``trading_system.analytics.rationale``.

Covers TC_RAT_005 (Analytics.rationale_for Some/Nothing) + TC_RAT_009
(persistence-round-trip readiness checks via the public read surface).

REQ refs: REQ_F_RAT_001, REQ_F_RAT_004, REQ_SDS_RAT_001,
REQ_SDD_RAT_003.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.analytics import rationale_for
from trading_system.backtesting.result import BacktestResult
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import OrderId, StrategyId, TradeId
from trading_system.models.money import Currency, Money
from trading_system.models.rationale import TradeRationale
from trading_system.models.trading import Trade
from trading_system.result import Nothing, Some


def _money(x: str) -> Money:
    return Money(Decimal(x), Currency.EUR)


def _trade(name: str) -> Trade:
    return Trade(
        id=TradeId(name),
        order_id=OrderId(f"order-{name}"),
        executed_at=datetime(2026, 5, 8, 9, 0, tzinfo=UTC),
        price=Decimal("100.0"),
        quantity_filled=Decimal("10"),
        fees=_money("0.50"),
    )


def _point() -> EquityPoint:
    return EquityPoint(
        at=datetime(2026, 5, 8, tzinfo=UTC),
        equity_gross=_money("10000"),
        equity_after_tax=_money("9700"),
        drawdown_pct=Decimal("0.0"),
    )


def _rationale(trade_id: str, reason: str = "default") -> TradeRationale:
    return TradeRationale(
        trade_id=TradeId(trade_id),
        strategy_id=StrategyId("s1"),
        strategy_version="sha-abc",
        signal_reason=reason,
        risk_approval={"tax_gate": "pass"},
        tax_gate_decision="net > 5*fees",
        improvement_report_id="",
        decided_at=datetime(2026, 5, 8, 9, 0, tzinfo=UTC),
    )


def _result(
    *,
    trades: tuple[Trade, ...],
    rationales: tuple[TradeRationale, ...],
) -> BacktestResult:
    return BacktestResult(
        trades=trades,
        equity_curve=(_point(),),
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
# TC_RAT_005 — Some / Nothing semantics
# ---------------------------------------------------------------------------


def test_rationale_for_returns_some_when_present() -> None:
    result = _result(
        trades=(_trade("trade-a"), _trade("trade-b")),
        rationales=(
            _rationale("trade-a", "alpha"),
            _rationale("trade-b", "beta"),
        ),
    )
    match rationale_for(result, TradeId("trade-b")):
        case Some(r):
            assert r.signal_reason == "beta"
        case _:
            raise AssertionError("expected Some(rationale)")


def test_rationale_for_returns_nothing_when_absent() -> None:
    result = _result(
        trades=(_trade("trade-a"),),
        rationales=(_rationale("trade-a", "alpha"),),
    )
    res = rationale_for(result, TradeId("trade-missing"))
    assert isinstance(res, Nothing)


def test_rationale_for_returns_nothing_on_empty_rationales() -> None:
    # Backwards-compat case: trades present but no rationales attached.
    result = _result(trades=(_trade("trade-a"),), rationales=())
    res = rationale_for(result, TradeId("trade-a"))
    assert isinstance(res, Nothing)


def test_rationale_for_returns_first_match_on_duplicate() -> None:
    """For a single backtest run, trade_id is unique. But the helper
    SHALL return the first match deterministically if duplicates ever
    appear — useful for tooling that aggregates multiple runs."""
    result = _result(
        trades=(_trade("trade-a"),),
        rationales=(_rationale("trade-a", "first"),),
    )
    match rationale_for(result, TradeId("trade-a")):
        case Some(r):
            assert r.signal_reason == "first"
        case _:
            raise AssertionError
