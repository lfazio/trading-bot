"""Structured product stress + liquidity drill — Phase 6
operational test.

Walks the documented admission flow + stress-scenario set against
realistic SP payoffs and asserts the documented gating behaviour
in REQ_F_STP_001..007.

Scenarios:

1. Happy path — a benign barrier product passes every gate;
   admission returns Ok(Decomposition) with the documented
   four-field shape.
2. Regime gate — BEAR / HIGH_VOL regimes reject; only BULL +
   SIDEWAYS admit.
3. Non-decomposable payoff — an unknown payoff type returns
   ``not_decomposable:no_decomposer:...`` (defensive coding
   against a future payoff-enum extension).
4. Total cap — proposed allocation that would push aggregate
   SP exposure past 10 % of equity returns ``cap_breach``.
5. Issuer concentration — a single issuer's share past 25 %
   returns ``issuer_concentration:...`` (REQ_SDD_ALG_014).
6. Turbo-stack ban — opening an SP on an underlying already
   carrying a turbo returns ``stack_with_turbo:<underlying>``
   (REQ_F_STP_007).
7. Stress gate — a high-leverage product that fails the
   30 %-vol-shock scenario returns ``stress_failed`` (the four
   stress scenarios in `stress.py` are crash × leverage, vol,
   correlation, each compared against the decomposition's
   worst_case_loss).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import (
    InstrumentClass,
    Stock,
    StructuredProduct,
    Turbo,
)
from trading_system.models.money import Currency, Money
from trading_system.models.phase import MarketRegime
from trading_system.portfolio.portfolio import Portfolio
from trading_system.result import Err, Ok
from trading_system.structured_products.admission import (
    AdmissionConfig,
    admit,
)
from trading_system.structured_products.decomposition import Decomposition


_EUR = Currency.EUR


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=_EUR)


def _portfolio() -> Portfolio:
    return Portfolio.empty(_eur("100000"))


def _underlying() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=_EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _sp(
    *,
    payoff: str = "BARRIER",
    issuer: str = "issuer-X",
    instrument_id: str = "SP-001",
    underlying_id: str | None = None,
) -> StructuredProduct:
    return StructuredProduct(
        id=InstrumentId(instrument_id),
        symbol=instrument_id,
        exchange="DE",
        currency=_EUR,
        cls=InstrumentClass.STRUCTURED,
        underlying=InstrumentId(underlying_id or "ASML.AS"),
        payoff=payoff,  # type: ignore[arg-type]
        issuer=issuer,
        barriers=(Decimal("0.70"),),
        notional=_eur("5000"),
    )


# ===========================================================================
# Scenario 1 — happy path
# ===========================================================================


def test_drill_benign_barrier_product_passes_admission() -> None:
    """REQ_F_STP_001..006 — a barrier product at 3 % allocation
    on a fresh portfolio (BULL regime, no turbo, no other SPs)
    SHALL pass every gate and return ``Ok(Decomposition)``."""
    sp = _sp()
    portfolio = _portfolio()
    result = admit(
        sp,
        proposed_allocation_pct=Decimal("0.03"),
        regime=MarketRegime.BULL,
        portfolio=portfolio,
    )
    assert isinstance(result, Ok), f"admission Err: {result}"
    decomp: Decomposition = result.value
    # Documented shape — four non-negative bounded fields.
    assert decomp.equity_equiv >= 0
    assert decomp.hidden_leverage >= 0
    assert Decimal(0) <= decomp.worst_case_loss <= Decimal(1)
    assert Decimal(0) <= decomp.break_even_prob <= Decimal(1)


# ===========================================================================
# Scenario 2 — regime gate
# ===========================================================================


@pytest.mark.parametrize(
    "regime",
    [MarketRegime.BEAR, MarketRegime.HIGH_VOL],
)
def test_drill_regime_gate_rejects_bear_and_high_vol(
    regime: MarketRegime,
) -> None:
    """REQ_F_STP_003 / REQ_F_STP_004 — only BULL + SIDEWAYS admit;
    BEAR and HIGH_VOL return ``regime_forbidden:<regime>``."""
    result = admit(
        _sp(),
        proposed_allocation_pct=Decimal("0.03"),
        regime=regime,
        portfolio=_portfolio(),
    )
    assert isinstance(result, Err)
    assert result.error.startswith("regime_forbidden:")
    assert regime.value in result.error


@pytest.mark.parametrize(
    "regime",
    [MarketRegime.BULL, MarketRegime.SIDEWAYS],
)
def test_drill_regime_gate_admits_bull_and_sideways(
    regime: MarketRegime,
) -> None:
    """REQ_F_STP_003 / REQ_F_STP_004 — BULL + SIDEWAYS pass the
    regime gate (subsequent gates may still reject)."""
    result = admit(
        _sp(),
        proposed_allocation_pct=Decimal("0.03"),
        regime=regime,
        portfolio=_portfolio(),
    )
    assert isinstance(result, Ok)


# ===========================================================================
# Scenario 3 — non-decomposable payoff
# ===========================================================================


def test_drill_unknown_payoff_returns_not_decomposable() -> None:
    """REQ_F_STP_002 / REQ_SDS_MOD_008 — an empty decomposer
    registry rejects every payoff. Use ``AdmissionConfig`` to
    inject a stripped registry so the SP that would otherwise
    pass hits the not_decomposable gate."""
    sp = _sp(payoff="BARRIER")
    cfg = AdmissionConfig(decomposers={})  # no decomposers ⇒ all reject
    result = admit(
        sp,
        proposed_allocation_pct=Decimal("0.03"),
        regime=MarketRegime.BULL,
        portfolio=_portfolio(),
        cfg=cfg,
    )
    assert isinstance(result, Err)
    assert result.error.startswith("not_decomposable:no_decomposer:")


# ===========================================================================
# Scenario 4 — total SP-allocation cap
# ===========================================================================


def test_drill_total_cap_rejects_excessive_allocation() -> None:
    """REQ_F_STP_001 — proposed_allocation > 10 % cap (default)
    on a fresh portfolio (no existing SP exposure) SHALL return
    ``cap_breach:...``."""
    result = admit(
        _sp(),
        proposed_allocation_pct=Decimal("0.11"),  # > 10 % cap
        regime=MarketRegime.BULL,
        portfolio=_portfolio(),
    )
    assert isinstance(result, Err)
    assert result.error.startswith("cap_breach:")


# ===========================================================================
# Scenario 5 — issuer concentration
# ===========================================================================


def test_drill_issuer_concentration_rejects_at_cap() -> None:
    """REQ_F_STP_006 / REQ_SDD_ALG_014 — single-issuer share > 25 %
    SHALL return ``issuer_concentration:...``. Tighten the
    issuer cap via AdmissionConfig so this drill provokes the
    gate at a clean 5 % proposed-allocation level rather than
    needing to stack a multi-step ledger first."""
    cfg = AdmissionConfig(issuer_cap=Decimal("0.04"))  # 4 % cap
    result = admit(
        _sp(),
        proposed_allocation_pct=Decimal("0.05"),  # > 4 % issuer cap
        regime=MarketRegime.BULL,
        portfolio=_portfolio(),
        cfg=cfg,
    )
    assert isinstance(result, Err)
    assert result.error.startswith("issuer_concentration:")


# ===========================================================================
# Scenario 6 — bad allocation pct
# ===========================================================================


def test_drill_negative_allocation_returns_bad_allocation() -> None:
    """REQ_F_STP_001 — proposed_allocation outside [0, 1] returns
    ``data:bad_allocation_pct:<value>``."""
    result = admit(
        _sp(),
        proposed_allocation_pct=Decimal("-0.05"),
        regime=MarketRegime.BULL,
        portfolio=_portfolio(),
    )
    assert isinstance(result, Err)
    assert result.error.startswith("data:bad_allocation_pct:")


def test_drill_allocation_above_one_returns_bad_allocation() -> None:
    """REQ_F_STP_001 — proposed_allocation > 1 (>100 %) returns
    ``data:bad_allocation_pct:...``."""
    result = admit(
        _sp(),
        proposed_allocation_pct=Decimal("1.5"),
        regime=MarketRegime.BULL,
        portfolio=_portfolio(),
    )
    assert isinstance(result, Err)
    assert result.error.startswith("data:bad_allocation_pct:")


# ===========================================================================
# Scenario 7 — turbo-stack ban
# ===========================================================================


def test_drill_turbo_stack_rejects_when_underlying_has_turbo() -> None:
    """REQ_F_STP_007 — SP on an underlying that already has a
    turbo position SHALL return ``stack_with_turbo:<underlying>``.

    The test builds a small portfolio that ALREADY carries a
    turbo on ASML.AS, then attempts to admit a structured
    product on the same underlying."""
    portfolio = _portfolio()

    # Plant a turbo position on ASML.AS by going through the
    # full Portfolio.apply path.
    from trading_system.models.identifiers import (
        OrderId,
        StrategyId,
        TradeId,
    )
    from trading_system.models.phase import AllocationBucket
    from trading_system.models.trading import (
        Order,
        OrderType,
        Side,
        StopLoss,
        Trade,
    )
    from datetime import UTC, datetime

    turbo = Turbo(
        id=InstrumentId("T-LONG"),
        symbol="T-LONG",
        exchange="DE",
        currency=_EUR,
        cls=InstrumentClass.TURBO,
        underlying=InstrumentId("ASML.AS"),
        direction="LONG",
        leverage=Decimal("5"),
        knockout=Decimal("90"),
        spread_pct=Decimal("0"),
    )
    order = Order(
        id=OrderId("o-turbo"),
        instrument=turbo,
        side=Side.BUY,
        quantity=Decimal("10"),
        type=OrderType.MARKET,
        stop_loss=StopLoss(price=Decimal("9")),
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        source_strategy=StrategyId("test"),
    )
    trade = Trade(
        id=TradeId("t-turbo"),
        order_id=order.id,
        executed_at=datetime(2026, 5, 1, tzinfo=UTC),
        price=Decimal("10"),
        quantity_filled=Decimal("10"),
        fees=_eur("0"),
    )
    from trading_system.tax.config import TaxConfig

    portfolio.apply(trade, order, AllocationBucket.TURBO, TaxConfig.default())
    assert portfolio.has_turbo_on(InstrumentId("ASML.AS"))

    # Now admit an SP on the same underlying.
    sp = _sp(underlying_id="ASML.AS")
    result = admit(
        sp,
        proposed_allocation_pct=Decimal("0.03"),
        regime=MarketRegime.BULL,
        portfolio=portfolio,
    )
    assert isinstance(result, Err)
    assert result.error.startswith("stack_with_turbo:")


# ===========================================================================
# Scenario 8 — stress gate (direct decomposition tests)
# ===========================================================================


def test_drill_stress_pass_for_safely_bounded_product() -> None:
    """REQ_F_STP_005 / REQ_SDD_ALG_013 — a decomposition whose
    worst_case_loss covers every stress scenario passes."""
    from trading_system.structured_products.stress import stress_pass

    # equity_equiv=0.5, hidden_leverage=0 ⇒ crash loss = 0.5×0.2 = 0.10;
    # vol loss = 0.5×0.30 = 0.15; corr loss = 0.5×0.15 = 0.075.
    # worst_case_loss = 0.20 ≥ every scenario.
    decomp = Decomposition(
        equity_equiv=Decimal("0.5"),
        hidden_leverage=Decimal("0"),
        worst_case_loss=Decimal("0.20"),
        break_even_prob=Decimal("0.7"),
    )
    assert stress_pass(decomp) is True


def test_drill_stress_fails_for_high_leverage_product() -> None:
    """REQ_F_STP_005 / REQ_SDD_ALG_013 — a leveraged decomposition
    whose worst_case_loss is too small to absorb the crash×
    leverage scenario fails. The crash scenario is
    equity_equiv × 0.20 × (1 + hidden_leverage); a 2x-leveraged
    product with equity_equiv=1 and worst_case_loss=0.30 sees
    crash_loss = 1 × 0.20 × 3 = 0.60 > 0.30 ⇒ fail."""
    from trading_system.structured_products.stress import stress_pass

    decomp = Decomposition(
        equity_equiv=Decimal("1"),
        hidden_leverage=Decimal("2"),  # 2x leveraged
        worst_case_loss=Decimal("0.30"),  # only 30 % stated
        break_even_prob=Decimal("0.5"),
    )
    assert stress_pass(decomp) is False


def test_drill_stress_pass_vacuous_for_zero_equity_equiv() -> None:
    """REQ_F_STP_005 — a cash-equivalent product (equity_equiv=0)
    has no scenario exposure; stress_pass returns True vacuously."""
    from trading_system.structured_products.stress import stress_pass

    decomp = Decomposition(
        equity_equiv=Decimal("0"),
        hidden_leverage=Decimal("0"),
        worst_case_loss=Decimal("0"),
        break_even_prob=Decimal("1"),
    )
    assert stress_pass(decomp) is True
