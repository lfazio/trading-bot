"""Tests for ``trading_system.structured_products``.

REQ refs:
- REQ_F_STP_001 — total SP allocation cap (10%).
- REQ_F_STP_002 — non-decomposable products rejected.
- REQ_F_STP_003 — admit only in BULL / SIDEWAYS.
- REQ_F_STP_004 — block in HIGH_VOL / BEAR.
- REQ_F_STP_005 — stress scenarios reject.
- REQ_F_STP_006 — issuer concentration cap.
- REQ_F_STP_007 — no SP / turbo stack on the same underlying.
- REQ_SDS_MOD_008 — non-decomposable rejected before allocation.
- REQ_SDD_ALG_012 — payoff decomposer table.
- REQ_SDD_ALG_013 — stress thresholds (-20% / vol x3 / corr -> 1).
- REQ_SDD_ALG_014 — issuer-concentration constant.

Covers TC_STP_001..007.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import (
    InstrumentClass,
    StructuredProduct,
    Turbo,
)
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket, MarketRegime
from trading_system.models.trading import (
    Order,
    OrderType,
    Side,
    StopLoss,
    Trade,
)
from trading_system.portfolio import Portfolio
from trading_system.result import Err, Ok
from trading_system.structured_products import (
    AdmissionConfig,
    Decomposition,
    admit,
    stress_pass,
)
from trading_system.structured_products.decomposers import PAYOFF_DECOMPOSERS
from trading_system.tax.config import TaxConfig

EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(year: int = 2026, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _sp(  # noqa: PLR0913 — test helper; mirrors StructuredProduct fields
    *,
    iid: str = "SP-1",
    payoff: str = "AUTOCALL",
    issuer: str = "BankA",
    underlying: str = "ASML.AS",
    barriers: tuple[Decimal, ...] = (Decimal("0.7"),),
    notional: str = "1000",
) -> StructuredProduct:
    return StructuredProduct(
        id=InstrumentId(iid),
        symbol=iid,
        exchange="DE",
        currency=EUR,
        cls=InstrumentClass.STRUCTURED,
        underlying=InstrumentId(underlying),
        payoff=payoff,
        issuer=issuer,
        barriers=barriers,
        notional=_eur(notional),
    )


def _turbo(underlying: str = "ASML.AS") -> Turbo:
    return Turbo(
        id=InstrumentId(f"T-{underlying}"),
        symbol="T",
        exchange="DE",
        currency=EUR,
        cls=InstrumentClass.TURBO,
        underlying=InstrumentId(underlying),
        direction="LONG",
        leverage=Decimal("5"),
        knockout=Decimal("90"),
        spread_pct=Decimal("0"),
    )


def _open_position(
    p: Portfolio,
    instrument: StructuredProduct | Turbo,
    *,
    qty: str,
    price: str,
    bucket: AllocationBucket,
) -> None:
    o = Order(
        id=OrderId(f"O-{instrument.id}"),
        instrument=instrument,
        side=Side.BUY,
        quantity=Decimal(qty),
        type=OrderType.MARKET,
        stop_loss=StopLoss(price=Decimal("1")),
        created_at=_ts(),
        source_strategy=StrategyId("S1"),
    )
    t = Trade(
        id=TradeId(f"T-{instrument.id}"),
        order_id=o.id,
        executed_at=_ts(),
        price=Decimal(price),
        quantity_filled=Decimal(qty),
        fees=_eur("0"),
    )
    p.apply(t, o, bucket, TaxConfig.default())


def _portfolio() -> Portfolio:
    return Portfolio.empty(_eur("100000"))


# ---------------------------------------------------------------------------
# TC_STP_001 — allocation cap
# ---------------------------------------------------------------------------


class TestAllocationCap:
    def test_under_cap_admits(self) -> None:
        p = _portfolio()
        match admit(_sp(), Decimal("0.05"), MarketRegime.SIDEWAYS, p):
            case Ok(_):
                pass
            case Err(e):
                raise AssertionError(f"unexpected Err: {e}")

    def test_at_cap_admits(self) -> None:
        p = _portfolio()
        match admit(_sp(), Decimal("0.10"), MarketRegime.SIDEWAYS, p):
            case Ok(_):
                pass
            case Err(e):
                raise AssertionError(f"unexpected Err: {e}")

    def test_above_cap_rejects(self) -> None:
        p = _portfolio()
        match admit(_sp(), Decimal("0.11"), MarketRegime.SIDEWAYS, p):
            case Err(reason):
                assert reason.startswith("cap_breach")
            case Ok(_):
                raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# TC_STP_002 — non-decomposable rejected before any allocation logic
# ---------------------------------------------------------------------------


def test_unknown_payoff_rejected_with_no_decomposer() -> None:
    # Use AdmissionConfig with empty decomposers; cap and other gates
    # would otherwise pass.
    cfg = AdmissionConfig(decomposers={})
    res = admit(_sp(), Decimal("0.01"), MarketRegime.SIDEWAYS, _portfolio(), cfg=cfg)
    match res:
        case Err(reason):
            assert reason.startswith("not_decomposable:no_decomposer")
        case Ok(_):
            raise AssertionError("expected Err")


def test_decomposer_returning_none_rejected() -> None:
    # AUTOCALL with empty barriers -> decomposer returns None.
    p = _portfolio()
    res = admit(
        _sp(barriers=()),
        Decimal("0.01"),
        MarketRegime.SIDEWAYS,
        p,
    )
    match res:
        case Err(reason):
            assert reason.startswith("not_decomposable:AUTOCALL")
        case Ok(_):
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# TC_STP_003 — payoff decomposers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payoff",
    list(PAYOFF_DECOMPOSERS.keys()),
)
def test_each_payoff_type_has_decomposer(payoff: str) -> None:
    decomposer = PAYOFF_DECOMPOSERS[payoff]
    product = _sp(payoff=payoff, barriers=(Decimal("2"),))
    decomp = decomposer(product)
    assert decomp is not None
    assert isinstance(decomp, Decomposition)


# ---------------------------------------------------------------------------
# TC_STP_004 — regime gating
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("regime", [MarketRegime.HIGH_VOL, MarketRegime.BEAR])
def test_high_vol_or_bear_regime_rejects(regime: MarketRegime) -> None:
    res = admit(_sp(), Decimal("0.05"), regime, _portfolio())
    match res:
        case Err(reason):
            assert reason.startswith("regime_forbidden")
        case Ok(_):
            raise AssertionError("expected Err")


@pytest.mark.parametrize("regime", [MarketRegime.BULL, MarketRegime.SIDEWAYS])
def test_bull_or_sideways_regime_admits(regime: MarketRegime) -> None:
    res = admit(_sp(), Decimal("0.05"), regime, _portfolio())
    match res:
        case Ok(_):
            pass
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")


# ---------------------------------------------------------------------------
# TC_STP_005 — stress scenarios
# ---------------------------------------------------------------------------


class TestStress:
    def test_stress_pass_when_loss_bounded(self) -> None:
        # equity_equiv 0.5, no leverage, worst_case 0.40:
        # crash 0.5*0.20*1 = 0.10; vol 0.5*0.30 = 0.15; corr
        # 0.5*0.15 = 0.075. All <= 0.40.
        decomp = Decomposition(
            equity_equiv=Decimal("0.5"),
            hidden_leverage=Decimal("0"),
            worst_case_loss=Decimal("0.40"),
            break_even_prob=Decimal("0.5"),
        )
        assert stress_pass(decomp) is True

    def test_stress_fails_when_crash_exceeds_worst_case(self) -> None:
        decomp = Decomposition(
            equity_equiv=Decimal("1.0"),
            hidden_leverage=Decimal("2"),  # 1.0 * 0.20 * 3 = 0.60
            worst_case_loss=Decimal("0.10"),
            break_even_prob=Decimal("0.5"),
        )
        assert stress_pass(decomp) is False

    def test_stress_zero_equity_equiv_passes_vacuously(self) -> None:
        decomp = Decomposition(
            equity_equiv=Decimal("0"),
            hidden_leverage=Decimal("0"),
            worst_case_loss=Decimal("0.05"),
            break_even_prob=Decimal("0.5"),
        )
        assert stress_pass(decomp) is True

    def test_admit_rejects_when_stress_fails(self) -> None:
        # Use a LEV_CERT with high leverage barrier so the decomposer
        # produces a leveraged Decomposition that fails stress.
        product = _sp(payoff="LEV_CERT", barriers=(Decimal("5"),))  # 4x hidden lev
        res = admit(product, Decimal("0.05"), MarketRegime.SIDEWAYS, _portfolio())
        match res:
            case Err(reason):
                assert reason.startswith("stress_failed")
            case Ok(_):
                raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# TC_STP_006 — issuer concentration cap
# ---------------------------------------------------------------------------


class TestIssuerCap:
    def test_existing_issuer_concentration_blocks(self) -> None:
        # The SP cap (10%) shadows the issuer cap (25%) in default
        # config — the issuer gate only binds when the operator
        # raises the SP cap (e.g., for SP-heavy mandates). Drive an
        # AdmissionConfig with a 60% SP cap so the issuer gate is
        # the binding constraint.
        cfg = AdmissionConfig(structured_cap=Decimal("0.60"))
        p = _portfolio()
        existing = _sp(iid="SP-existing", issuer="BankA", notional="24000")
        # equity = 100000; price 1, qty 24000 -> marked 24000 -> 24% of equity.
        _open_position(p, existing, qty="24000", price="1", bucket=AllocationBucket.STRUCTURED)
        new = _sp(iid="SP-new", issuer="BankA", notional="5000")
        match admit(new, Decimal("0.05"), MarketRegime.SIDEWAYS, p, cfg=cfg):
            case Err(reason):
                assert reason.startswith("issuer_concentration:BankA")
            case Ok(_):
                raise AssertionError("expected Err")

    def test_different_issuer_unaffected(self) -> None:
        p = _portfolio()
        existing = _sp(iid="SP-existing", issuer="BankA", notional="9000")
        _open_position(p, existing, qty="9000", price="1", bucket=AllocationBucket.STRUCTURED)
        # Total SP exposure already 9% — try 1% more from BankB; the
        # SP-cap is 10%, so 9 + 1 = 10 == cap (allowed).
        new = _sp(iid="SP-new", issuer="BankB", notional="1000")
        match admit(new, Decimal("0.01"), MarketRegime.SIDEWAYS, p):
            case Ok(_):
                pass
            case Err(e):
                raise AssertionError(f"unexpected Err: {e}")


# ---------------------------------------------------------------------------
# TC_STP_007 — no SP / turbo stack on the same underlying
# ---------------------------------------------------------------------------


def test_admit_rejects_when_turbo_on_same_underlying() -> None:
    p = _portfolio()
    _open_position(p, _turbo("ASML.AS"), qty="10", price="20", bucket=AllocationBucket.TURBO)
    res = admit(
        _sp(underlying="ASML.AS"),
        Decimal("0.01"),
        MarketRegime.SIDEWAYS,
        p,
    )
    match res:
        case Err(reason):
            assert reason.startswith("stack_with_turbo")
        case Ok(_):
            raise AssertionError("expected Err")


def test_admit_allows_when_turbo_on_different_underlying() -> None:
    p = _portfolio()
    _open_position(p, _turbo("OTHER.PA"), qty="10", price="20", bucket=AllocationBucket.TURBO)
    res = admit(
        _sp(underlying="ASML.AS"),
        Decimal("0.01"),
        MarketRegime.SIDEWAYS,
        p,
    )
    match res:
        case Ok(_):
            pass
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")


# ---------------------------------------------------------------------------
# AdmissionConfig validation
# ---------------------------------------------------------------------------


class TestAdmissionConfig:
    def test_structured_cap_outside_unit_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="structured_cap"):
            AdmissionConfig(structured_cap=Decimal("1.5"))

    def test_issuer_cap_outside_unit_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="issuer_cap"):
            AdmissionConfig(issuer_cap=Decimal("-0.1"))

    def test_empty_allowed_regimes_rejected(self) -> None:
        with pytest.raises(ValueError, match="allowed_regimes"):
            AdmissionConfig(allowed_regimes=frozenset())

    def test_proposed_allocation_outside_unit_interval_rejected(self) -> None:
        match admit(_sp(), Decimal("1.5"), MarketRegime.SIDEWAYS, _portfolio()):
            case Err(reason):
                assert reason.startswith("data:bad_allocation_pct")
            case Ok(_):
                raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# Decomposition invariants — Phase-8 C1 coverage cleanup (REQ_SDD_DAT_*)
# ---------------------------------------------------------------------------


class TestDecompositionInvariants:
    """REQ_SDD_ALG_012 — the four-field signature SHALL validate its
    own bounds at construction. Targeted Err-branch tests to lift
    `trading_system/structured_products/decomposition.py` from 69%
    to 100% coverage."""

    def _ok_kwargs(self) -> dict:
        return {
            "equity_equiv": Decimal("0.85"),
            "hidden_leverage": Decimal("0"),
            "worst_case_loss": Decimal("0.40"),
            "break_even_prob": Decimal("0.65"),
        }

    def test_negative_equity_equiv_rejected(self) -> None:
        kwargs = self._ok_kwargs()
        kwargs["equity_equiv"] = Decimal("-0.01")
        with pytest.raises(ValueError, match="equity_equiv"):
            Decomposition(**kwargs)

    def test_negative_hidden_leverage_rejected(self) -> None:
        kwargs = self._ok_kwargs()
        kwargs["hidden_leverage"] = Decimal("-0.5")
        with pytest.raises(ValueError, match="hidden_leverage"):
            Decomposition(**kwargs)

    def test_worst_case_loss_above_one_rejected(self) -> None:
        kwargs = self._ok_kwargs()
        kwargs["worst_case_loss"] = Decimal("1.5")
        with pytest.raises(ValueError, match="worst_case_loss"):
            Decomposition(**kwargs)

    def test_worst_case_loss_below_zero_rejected(self) -> None:
        kwargs = self._ok_kwargs()
        kwargs["worst_case_loss"] = Decimal("-0.01")
        with pytest.raises(ValueError, match="worst_case_loss"):
            Decomposition(**kwargs)

    def test_break_even_prob_above_one_rejected(self) -> None:
        kwargs = self._ok_kwargs()
        kwargs["break_even_prob"] = Decimal("1.5")
        with pytest.raises(ValueError, match="break_even_prob"):
            Decomposition(**kwargs)

    def test_break_even_prob_below_zero_rejected(self) -> None:
        kwargs = self._ok_kwargs()
        kwargs["break_even_prob"] = Decimal("-0.5")
        with pytest.raises(ValueError, match="break_even_prob"):
            Decomposition(**kwargs)

    def test_endpoints_accepted(self) -> None:
        """``[0, 1]`` is inclusive on both ends — equity_equiv = 0
        and break_even_prob = 1.0 SHALL be valid (zero exposure;
        guaranteed break-even)."""
        Decomposition(
            equity_equiv=Decimal("0"),
            hidden_leverage=Decimal("0"),
            worst_case_loss=Decimal("0"),
            break_even_prob=Decimal("0"),
        )
        Decomposition(
            equity_equiv=Decimal("2.0"),
            hidden_leverage=Decimal("3.0"),
            worst_case_loss=Decimal("1.0"),
            break_even_prob=Decimal("1.0"),
        )
