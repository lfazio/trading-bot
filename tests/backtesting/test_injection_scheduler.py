"""Tests for ``trading_system.backtesting.injection_scheduler``.

Covers REQ_F_BCT_007, REQ_F_CFL_004 (timeline replay never inflates
returns — the test verifies that the equity_excl_injections series
strips them out by the time the engine reports).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.backtesting.injection_scheduler import InjectionScheduler
from trading_system.capital_flow.flow import CapitalFlow
from trading_system.models.flow import EquityPoint, Injection
from trading_system.models.money import Currency, Money
from trading_system.portfolio.portfolio import Portfolio

EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


class TestMaybeApply:
    def test_no_pending_returns_empty(self) -> None:
        sched = InjectionScheduler.from_schedule([])
        cf = CapitalFlow(initial=_eur("1000"))
        p = Portfolio.empty(_eur("1000"))
        assert sched.maybe_apply(_ts(1), cf, p) == []

    def test_applies_due_injection(self) -> None:
        inj = Injection(amount=_eur("500"), at=_ts(5))
        sched = InjectionScheduler.from_schedule([inj])
        cf = CapitalFlow(initial=_eur("1000"))
        p = Portfolio.empty(_eur("1000"))
        applied = sched.maybe_apply(_ts(5), cf, p)
        assert applied == [inj]
        assert p.cash() == _eur("1500")
        assert cf.injections == [inj]
        assert sched.remaining == 0

    def test_skips_future_injections(self) -> None:
        future = Injection(amount=_eur("500"), at=_ts(10))
        sched = InjectionScheduler.from_schedule([future])
        cf = CapitalFlow(initial=_eur("1000"))
        p = Portfolio.empty(_eur("1000"))
        assert sched.maybe_apply(_ts(5), cf, p) == []
        assert p.cash() == _eur("1000")
        assert sched.remaining == 1

    def test_unsorted_input_resorted(self) -> None:
        a = Injection(amount=_eur("100"), at=_ts(1))
        b = Injection(amount=_eur("200"), at=_ts(5))
        sched = InjectionScheduler.from_schedule([b, a])  # out of order
        cf = CapitalFlow(initial=_eur("1000"))
        p = Portfolio.empty(_eur("1000"))
        applied = sched.maybe_apply(_ts(10), cf, p)
        assert applied == [a, b]  # ascending by .at
        assert p.cash() == _eur("1300")

    def test_idempotent_after_drain(self) -> None:
        inj = Injection(amount=_eur("500"), at=_ts(5))
        sched = InjectionScheduler.from_schedule([inj])
        cf = CapitalFlow(initial=_eur("1000"))
        p = Portfolio.empty(_eur("1000"))
        sched.maybe_apply(_ts(5), cf, p)
        # Second call at same / later t — nothing to do.
        assert sched.maybe_apply(_ts(10), cf, p) == []
        assert p.cash() == _eur("1500")


class TestEquityExclInjectionsStrip:
    """Cross-checks that injections do not inflate the canonical
    performance series (REQ_F_CFL_002 / REQ_F_CFL_004 at the engine
    integration level)."""

    def test_strip_after_replay_yields_only_pnl_growth(self) -> None:
        # Start at 1000; inject 500 at day 5; "PnL" = 0 (no trades);
        # final equity = 1500. equity_excl_injections at day 5
        # should be 1000 (the original capital).
        sched = InjectionScheduler.from_schedule([Injection(amount=_eur("500"), at=_ts(5))])
        cf = CapitalFlow(initial=_eur("1000"))
        p = Portfolio.empty(_eur("1000"))
        sched.maybe_apply(_ts(5), cf, p)
        # Build a curve point reflecting current equity.
        curve = [
            EquityPoint(
                at=_ts(5),
                equity_gross=p.equity_gross(),
                equity_after_tax=p.equity_after_tax(),
                drawdown_pct=Decimal(0),
            )
        ]
        stripped = cf.equity_excl_injections(curve)
        assert stripped == [Decimal("1000")]
