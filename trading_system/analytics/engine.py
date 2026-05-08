"""``Analytics`` — read-only metrics layer over portfolio + capital flow.

Wraps the canonical sources of truth so the dashboard and any reporting
consumer can compute equity curves, drawdown series, exposure, Sharpe,
totals, and Phase-6 attribution without poking at private state of
``Portfolio`` / ``CapitalFlow``.

Relies on the existing pieces rather than reimplementing them:
- ``Portfolio.equity_curve`` — already populated per tick by the engine.
- ``Portfolio.attribution()`` — Phase-6 NAV / by-strategy / by-class rows.
- ``CapitalFlow.equity_excl_injections`` — canonical performance series.
- ``backtesting.walk_forward.sharpe_ratio`` — annualized Sharpe.

The class is intentionally read-only; mutation flows through Portfolio
and CapitalFlow only (REQ_SDS_MOD_015's spirit applies one layer up at
the dashboard).

REQ refs: REQ_F_PRT_002 (attribution), REQ_NF_LOG_001 (timestamped
events surfaced as the Trade tuple), REQ_SDS_MOD_005 (canonical
equity_excl_injections series), REQ_SDS_MOD_011 (after-tax equity is
the canonical reference).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.backtesting.walk_forward import sharpe_ratio
from trading_system.capital_flow.flow import CapitalFlow
from trading_system.models.flow import EquityPoint
from trading_system.models.instrument import InstrumentClass
from trading_system.models.money import Money
from trading_system.models.phase import AllocationBucket
from trading_system.models.trading import Trade
from trading_system.portfolio.portfolio import AttributionRow, Portfolio
from trading_system.risk.mapping import buckets_for_class


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    """Headline metrics consumed by the dashboard."""

    total_return_after_tax_pct: Decimal
    total_return_gross_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_after_tax: Decimal
    realized_gross: Money
    realized_after_tax: Money
    dividends_gross: Money
    dividends_after_tax: Money
    fees_total: Money
    trade_count: int


@dataclass(slots=True)
class Analytics:
    """Read-only performance + monitoring view."""

    portfolio: Portfolio
    capital_flow: CapitalFlow
    trades: tuple[Trade, ...] = ()

    # ------------------------------------------------------------------
    # Series outputs
    # ------------------------------------------------------------------

    def equity_curve(self) -> tuple[EquityPoint, ...]:
        """The canonical after-tax equity curve as a frozen tuple."""
        return tuple(self.portfolio.equity_curve)

    def equity_excl_injections(self) -> tuple[Decimal, ...]:
        """Performance series with cumulative external injections
        stripped (REQ_F_CFL_002 / REQ_SDS_MOD_005)."""
        return tuple(self.capital_flow.equity_excl_injections(self.portfolio.equity_curve))

    def drawdown_series(self) -> tuple[Decimal, ...]:
        """Drawdown percentage at each equity-curve point. Already
        computed on the curve itself; this method exposes it as a
        flat sequence for plotters."""
        return tuple(p.drawdown_pct for p in self.portfolio.equity_curve)

    # ------------------------------------------------------------------
    # Scalar summaries
    # ------------------------------------------------------------------

    def max_drawdown(self) -> Decimal:
        """Peak drawdown observed across the equity curve."""
        if not self.portfolio.equity_curve:
            return Decimal(0)
        return max(p.drawdown_pct for p in self.portfolio.equity_curve)

    def sharpe(self) -> Decimal:
        """Annualized Sharpe of the after-tax equity series. Uses the
        same helper the walk-forward harness uses so Sharpe values
        are comparable across analytics and validation paths."""
        return sharpe_ratio(self.portfolio.equity_curve)

    def total_return_after_tax(self) -> Decimal:
        """Net performance excluding injections, expressed as a
        Decimal fraction of the initial capital. Returns ``Decimal(0)``
        for an empty curve."""
        if not self.portfolio.equity_curve:
            return Decimal(0)
        excl = self.capital_flow.equity_excl_injections(self.portfolio.equity_curve)
        if not excl:
            return Decimal(0)
        initial = self.capital_flow.initial.amount
        if initial == 0:
            return Decimal(0)
        return (excl[-1] - initial) / initial

    def total_return_gross(self) -> Decimal:
        """Gross-of-tax counterpart to ``total_return_after_tax``."""
        if not self.portfolio.equity_curve:
            return Decimal(0)
        initial = self.capital_flow.initial.amount
        if initial == 0:
            return Decimal(0)
        last = self.portfolio.equity_curve[-1].equity_gross.amount
        injected = self.capital_flow.cumulative_injected_at(
            self.portfolio.equity_curve[-1].at
        ).amount
        return (last - initial - injected) / initial

    def fees_total(self) -> Money:
        """Sum of executed broker fees across all trades."""
        if not self.trades:
            return Money(Decimal(0), self.portfolio.currency)
        total = Money(Decimal(0), self.portfolio.currency)
        for t in self.trades:
            total = total + t.fees
        return total

    def exposure_by_class(self) -> dict[InstrumentClass, Decimal]:
        """Share of equity allocated to each ``InstrumentClass``.

        Lumps the AllocationBucket exposures via
        ``risk.mapping.buckets_for_class`` so STOCK + TACTICAL roll
        up under ``InstrumentClass.STOCK`` (REQ_SDD_TYP_004).
        """
        out: dict[InstrumentClass, Decimal] = {}
        for cls in InstrumentClass:
            buckets: tuple[AllocationBucket, ...] = buckets_for_class(cls)
            total = sum(
                (self.portfolio.exposure_pct(b) for b in buckets),
                start=Decimal(0),
            )
            out[cls] = total
        return out

    def attribution(self) -> tuple[AttributionRow, ...]:
        """Pass-through to ``Portfolio.attribution`` (REQ_F_PRT_002)."""
        return self.portfolio.attribution()

    # ------------------------------------------------------------------
    # Headline summary
    # ------------------------------------------------------------------

    def summary(self) -> PerformanceSummary:
        """Aggregate the headline metrics used by the dashboard."""
        return PerformanceSummary(
            total_return_after_tax_pct=self.total_return_after_tax(),
            total_return_gross_pct=self.total_return_gross(),
            max_drawdown_pct=self.max_drawdown(),
            sharpe_after_tax=self.sharpe(),
            realized_gross=self.portfolio.realized_gross(),
            realized_after_tax=self.portfolio.realized_after_tax(),
            dividends_gross=self.portfolio.dividends_gross(),
            dividends_after_tax=self.portfolio.dividends_after_tax(),
            fees_total=self.fees_total(),
            trade_count=len(self.trades),
        )
