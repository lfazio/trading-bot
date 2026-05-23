"""Per-strategy attribution + portfolio NAV summary.

Phase-6 reporting surface — pure-function over a ``BacktestResult``
(or a paper-trading runtime's recorded trades + equity points).
Produces a small dataclass the operator dashboard / reports view
can render alongside the existing 5-file bundle.

The v1 attribution is **notional-weighted**, not full FIFO/LIFO
cost-basis matching. We surface:

- per-strategy trade count, turnover (sum of |price × qty|),
  and fees paid;
- portfolio totals for the same quantities;
- the proportional P&L share each strategy contributed (= its
  turnover share of the portfolio's realized P&L over the
  window).

Cost-basis matched per-strategy P&L is deferred to a future
amendment; tracking entries → exits across multi-strategy
overlaps needs a position-history time series that the engine
doesn't currently surface. The notional-weighted approximation
is enough to flag strategies that move the needle (high
turnover, high fees) vs. strategies that idle.

REQ refs:
- REQ_F_RPT_001 follow-up — the reports panel surfaces
  per-strategy attribution alongside the equity curve.
- Phase-6 NAV-style reporting (TASKS.md open checkbox at
  ``analytics/``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.backtesting.result import BacktestResult
from trading_system.models.money import Currency, Money
from trading_system.models.rationale import TradeRationale
from trading_system.models.trading import Trade


_UNKNOWN_STRATEGY = "unknown"


@dataclass(frozen=True, slots=True)
class StrategyAttribution:
    """One row of the per-strategy attribution table."""

    strategy_id: str
    trade_count: int
    total_turnover: Money
    total_fees: Money
    turnover_share_pct: Decimal  # 0..100; share of portfolio turnover
    realized_pnl_proxy: Money  # turnover-weighted slice of realized PnL


@dataclass(frozen=True, slots=True)
class AttributionReport:
    """Aggregate NAV roll-up + per-strategy breakdown."""

    by_strategy: tuple[StrategyAttribution, ...]
    portfolio_trade_count: int
    portfolio_turnover: Money
    portfolio_fees: Money
    portfolio_realized_pnl: Money  # from BacktestResult.realized_after_tax
    currency: Currency


def _strategy_for_trade(
    trade: Trade, rationales_by_id: dict[str, TradeRationale]
) -> str:
    """Look up the strategy that emitted ``trade``. Returns the
    documented ``"unknown"`` sentinel when no matching rationale
    is present (e.g., legacy runs without the audit trail)."""
    rationale = rationales_by_id.get(str(trade.id))
    if rationale is None:
        return _UNKNOWN_STRATEGY
    return str(rationale.strategy_id)


def attribution_from_result(result: BacktestResult) -> AttributionReport:
    """Compute the NAV roll-up + per-strategy attribution.

    Pure function — same input ⇒ same output. Trades with no
    matching ``TradeRationale`` land under the ``"unknown"``
    bucket so legacy runs without the audit trail still produce
    a valid report.
    """
    currency = result.final_equity_after_tax.currency
    rationales_by_id = {str(r.trade_id): r for r in result.rationales}

    # Per-strategy accumulators.
    counts: dict[str, int] = {}
    turnover: dict[str, Decimal] = {}
    fees: dict[str, Decimal] = {}
    for trade in result.trades:
        sid = _strategy_for_trade(trade, rationales_by_id)
        notional = trade.price * trade.quantity_filled
        counts[sid] = counts.get(sid, 0) + 1
        turnover[sid] = turnover.get(sid, Decimal("0")) + notional
        # Trade.fees is a Money — defensive currency coercion.
        if trade.fees.currency != currency:
            # Mixed currencies — bail out of the fee sum for this
            # strategy with a zero, surfaced to the operator via
            # the report rather than crashing the run.
            continue
        fees[sid] = fees.get(sid, Decimal("0")) + trade.fees.amount

    portfolio_turnover = sum(turnover.values(), start=Decimal("0"))
    portfolio_fees = sum(fees.values(), start=Decimal("0"))
    portfolio_realized = result.realized_after_tax.amount

    rows: list[StrategyAttribution] = []
    # Stable iteration order: alphabetical strategy_id for replay
    # determinism (REQ_NF_REP_001).
    for sid in sorted(counts.keys()):
        sturnover = turnover.get(sid, Decimal("0"))
        if portfolio_turnover > 0:
            share_pct = (sturnover / portfolio_turnover * Decimal("100")).quantize(
                Decimal("0.01")
            )
            pnl_share = (
                (sturnover / portfolio_turnover) * portfolio_realized
            ).quantize(Decimal("0.0001"))
        else:
            share_pct = Decimal("0.00")
            pnl_share = Decimal("0.0000")
        rows.append(
            StrategyAttribution(
                strategy_id=sid,
                trade_count=counts[sid],
                total_turnover=Money(sturnover, currency),
                total_fees=Money(fees.get(sid, Decimal("0")), currency),
                turnover_share_pct=share_pct,
                realized_pnl_proxy=Money(pnl_share, currency),
            )
        )
    return AttributionReport(
        by_strategy=tuple(rows),
        portfolio_trade_count=len(result.trades),
        portfolio_turnover=Money(portfolio_turnover, currency),
        portfolio_fees=Money(portfolio_fees, currency),
        portfolio_realized_pnl=result.realized_after_tax,
        currency=currency,
    )
