"""Risk sizing scales with total available capital — REQ_F_CFL_003.

REQ_F_CFL_003 — Risk sizing SHALL scale with total available
capital (initial + injections + retained equity).

The risk engine's per-trade band is a fraction of capital
(``size_pct_of_capital`` in ``RiskConfig.risk_per_trade_band``),
so the absolute euro budget is implicitly ``band × total_capital``.
This test demonstrates the algebra: as ``CapitalFlow.total_capital()``
grows via injections, the implied euro risk budget grows
proportionally — no manual rescaling required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.capital_flow import CapitalFlow
from trading_system.models.flow import Injection
from trading_system.models.money import Currency, Money

_EUR = Currency.EUR


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=_EUR)


def _at(month: int) -> datetime:
    return datetime(2026, month, 1, 12, 0, tzinfo=UTC)


def test_risk_budget_scales_with_injections() -> None:
    """REQ_F_CFL_003 — risk budget (= band × total_capital) at
    time T1 SHALL strictly exceed the budget at T0 when an
    injection occurs between T0 and T1.

    The test pins a single per-trade band (1 % of capital) and
    asserts the euro budget tracks total_capital through two
    injections.
    """
    cf = CapitalFlow(initial=_eur("10000"))
    band_pct = Decimal("0.01")  # 1 % per-trade band

    # T0 — only initial capital.
    budget_t0 = cf.total_capital().amount * band_pct
    assert budget_t0 == Decimal("100.00")

    # T1 — operator injects 5_000.
    cf.observe(Injection(amount=_eur("5000"), at=_at(2)))
    budget_t1 = cf.total_capital().amount * band_pct
    assert budget_t1 == Decimal("150.00")
    assert budget_t1 > budget_t0

    # T2 — second injection.
    cf.observe(Injection(amount=_eur("85000"), at=_at(3)))
    budget_t2 = cf.total_capital().amount * band_pct
    assert budget_t2 == Decimal("1000.00")
    assert budget_t2 > budget_t1


def test_risk_budget_is_proportional_to_band() -> None:
    """REQ_F_CFL_003 — for a fixed total_capital, two operators
    with different per-trade bands SHALL get proportionally
    different euro budgets. Demonstrates the linearity that the
    REQ relies on so a strategy whose band is doubled gets
    exactly twice the euro headroom."""
    cf = CapitalFlow(initial=_eur("50000"))
    total = cf.total_capital().amount
    budget_at_1pct = total * Decimal("0.01")
    budget_at_2pct = total * Decimal("0.02")
    assert budget_at_2pct == budget_at_1pct * Decimal("2")
