"""Tests for ``trading_system.phase_engine.engine``.

Covers natural-phase resolution (REQ_F_CAP_002, REQ_F_CAP_003),
hysteresis on downgrade (REQ_F_CAP_005, REQ_SDD_ALG_002),
constraint distribution (REQ_SDS_FLO_002), and construction-time
invariant validation (REQ_F_CAP_012, REQ_SDD_ALG_020).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.money import Currency, Money
from trading_system.models.phase import (
    AllocationBucket,
    Phase,
    PhaseConstraints,
)
from trading_system.phase_engine.engine import (
    PhaseEngine,
    natural_phase_for_amount,
    resolve_with_hysteresis,
)

EUR = Currency.EUR

DEFAULT_BOUNDS = [
    Decimal("3000"),
    Decimal("10000"),
    Decimal("50000"),
    Decimal("200000"),
    Decimal("1000000"),
]


def constraints(*, vol_cap: Decimal | None = None) -> PhaseConstraints:
    return PhaseConstraints(
        max_positions=3,
        max_trades_per_month=4,
        allocation_targets={
            AllocationBucket.STOCK: Decimal("0.90"),
            AllocationBucket.TACTICAL: Decimal("0.10"),
        },
        turbo_exposure_max=Decimal("0.05"),
        risk_per_trade_band=(Decimal("0.01"), Decimal("0.02")),
        max_drawdown=Decimal("0.15"),
        portfolio_vol_cap=vol_cap,
    )


def six_phase_constraints() -> dict[Phase, PhaseConstraints]:
    """Six valid PhaseConstraints, with vol caps on Phase 5 and 6."""
    return {
        Phase.ONE: constraints(),
        Phase.TWO: constraints(),
        Phase.THREE: constraints(),
        Phase.FOUR: constraints(),
        Phase.FIVE: constraints(vol_cap=Decimal("0.12")),
        Phase.SIX: constraints(vol_cap=Decimal("0.08")),
    }


# ---------------------------------------------------------------------------
# natural_phase_for_amount
# ---------------------------------------------------------------------------


class TestNaturalPhaseForAmount:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            (Decimal("0"), Phase.ONE),
            (Decimal("2999.99"), Phase.ONE),
            (Decimal("3000"), Phase.TWO),
            (Decimal("9999.99"), Phase.TWO),
            (Decimal("10000"), Phase.THREE),
            (Decimal("49999.99"), Phase.THREE),
            (Decimal("50000"), Phase.FOUR),
            (Decimal("199999.99"), Phase.FOUR),
            (Decimal("200000"), Phase.FIVE),
            (Decimal("999999.99"), Phase.FIVE),
            (Decimal("1000000"), Phase.SIX),
            (Decimal("99999999"), Phase.SIX),
        ],
    )
    def test_resolves_each_band(self, amount: Decimal, expected: Phase) -> None:
        assert natural_phase_for_amount(amount, DEFAULT_BOUNDS) == expected


# ---------------------------------------------------------------------------
# resolve_with_hysteresis
# ---------------------------------------------------------------------------


class TestResolveWithHysteresis:
    def test_upgrade_is_immediate(self) -> None:
        # 2_999 -> 3_001: target=Phase.TWO, current=Phase.ONE; upgrades immediately.
        assert (
            resolve_with_hysteresis(
                amount=Decimal("3001"),
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                current=Phase.ONE,
            )
            == Phase.TWO
        )

    def test_downgrade_blocked_inside_hysteresis_band(self) -> None:
        # Sitting at Phase.TWO; amount drops to 2_800 (above 3000 * 0.90 = 2700).
        # Natural phase is ONE, but hysteresis blocks the downgrade.
        assert (
            resolve_with_hysteresis(
                amount=Decimal("2800"),
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                current=Phase.TWO,
            )
            == Phase.TWO
        )

    def test_downgrade_below_hysteresis_threshold(self) -> None:
        # Same scenario but below 2700; downgrade fires.
        assert (
            resolve_with_hysteresis(
                amount=Decimal("2699"),
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                current=Phase.TWO,
            )
            == Phase.ONE
        )

    def test_zero_hysteresis_downgrades_at_boundary(self) -> None:
        assert (
            resolve_with_hysteresis(
                amount=Decimal("2999"),
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0"),
                current=Phase.TWO,
            )
            == Phase.ONE
        )

    def test_phase_one_floor(self) -> None:
        # No phase below ONE; downgrade is a no-op even at zero amount.
        assert (
            resolve_with_hysteresis(
                amount=Decimal("0"),
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                current=Phase.ONE,
            )
            == Phase.ONE
        )

    def test_no_change_when_target_equals_current(self) -> None:
        assert (
            resolve_with_hysteresis(
                amount=Decimal("5000"),
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                current=Phase.TWO,
            )
            == Phase.TWO
        )

    def test_skip_levels_on_upgrade(self) -> None:
        # 1: jump straight from ONE to FIVE.
        assert (
            resolve_with_hysteresis(
                amount=Decimal("250000"),
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                current=Phase.ONE,
            )
            == Phase.FIVE
        )

    def test_downgrade_only_one_step_per_call(self) -> None:
        # Sitting at Phase.SIX; amount falls to 50_000 (natural=FOUR).
        # A single resolve call moves to FOUR (the natural phase) only
        # if hysteresis allows; current=SIX, lower bound=1_000_000,
        # threshold=900_000, amount=50_000 < threshold → downgrade fires.
        assert (
            resolve_with_hysteresis(
                amount=Decimal("50000"),
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                current=Phase.SIX,
            )
            == Phase.FOUR
        )


# ---------------------------------------------------------------------------
# PhaseEngine class
# ---------------------------------------------------------------------------


class TestPhaseEngine:
    def test_construction_validates_six_phases(self) -> None:
        all_phases = six_phase_constraints()
        engine = PhaseEngine(
            bounds=DEFAULT_BOUNDS,
            hysteresis=Decimal("0.10"),
            constraints=all_phases,
        )
        assert engine.current() == Phase.ONE

    def test_missing_phase_rejected(self) -> None:
        all_phases = six_phase_constraints()
        del all_phases[Phase.FOUR]
        with pytest.raises(ValueError, match="missing entry for"):
            PhaseEngine(
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                constraints=all_phases,
            )

    def test_bounds_wrong_length_rejected(self) -> None:
        with pytest.raises(ValueError, match="bounds must have exactly 5"):
            PhaseEngine(
                bounds=[Decimal("3000")],
                hysteresis=Decimal("0.10"),
                constraints=six_phase_constraints(),
            )

    def test_bounds_not_ascending_rejected(self) -> None:
        with pytest.raises(ValueError, match="strictly ascending"):
            PhaseEngine(
                bounds=[
                    Decimal("3000"),
                    Decimal("2000"),
                    Decimal("50000"),
                    Decimal("200000"),
                    Decimal("1000000"),
                ],
                hysteresis=Decimal("0.10"),
                constraints=six_phase_constraints(),
            )

    @pytest.mark.parametrize("h", [Decimal("-0.01"), Decimal("1"), Decimal("1.5")])
    def test_invalid_hysteresis_rejected(self, h: Decimal) -> None:
        with pytest.raises(ValueError, match="hysteresis"):
            PhaseEngine(
                bounds=DEFAULT_BOUNDS,
                hysteresis=h,
                constraints=six_phase_constraints(),
            )

    def test_phase5_vol_cap_required(self) -> None:
        cs = six_phase_constraints()
        cs[Phase.FIVE] = constraints(vol_cap=None)
        with pytest.raises(ValueError, match=r"Phase\.FIVE.*portfolio_vol_cap"):
            PhaseEngine(
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                constraints=cs,
            )

    def test_phase6_vol_cap_required(self) -> None:
        cs = six_phase_constraints()
        cs[Phase.SIX] = constraints(vol_cap=None)
        with pytest.raises(ValueError, match=r"Phase\.SIX.*portfolio_vol_cap"):
            PhaseEngine(
                bounds=DEFAULT_BOUNDS,
                hysteresis=Decimal("0.10"),
                constraints=cs,
            )

    def test_resolve_updates_current(self) -> None:
        engine = PhaseEngine(
            bounds=DEFAULT_BOUNDS,
            hysteresis=Decimal("0.10"),
            constraints=six_phase_constraints(),
        )
        assert engine.current() == Phase.ONE
        engine.resolve(Money(Decimal("5000"), EUR))
        assert engine.current() == Phase.TWO

    def test_resolve_hysteresis_persists_across_calls(self) -> None:
        engine = PhaseEngine(
            bounds=DEFAULT_BOUNDS,
            hysteresis=Decimal("0.10"),
            constraints=six_phase_constraints(),
            initial_phase=Phase.TWO,
        )
        # Drop into the hysteresis band — phase stays at TWO.
        engine.resolve(Money(Decimal("2800"), EUR))
        assert engine.current() == Phase.TWO
        # Drop further below the hysteresis threshold — phase falls to ONE.
        engine.resolve(Money(Decimal("2699"), EUR))
        assert engine.current() == Phase.ONE

    def test_constraints_for_returns_specified_phase(self) -> None:
        engine = PhaseEngine(
            bounds=DEFAULT_BOUNDS,
            hysteresis=Decimal("0.10"),
            constraints=six_phase_constraints(),
        )
        pc5 = engine.constraints_for(Phase.FIVE)
        assert pc5.portfolio_vol_cap == Decimal("0.12")
        pc6 = engine.constraints_for(Phase.SIX)
        assert pc6.portfolio_vol_cap == Decimal("0.08")

    def test_initial_phase_other_than_one(self) -> None:
        engine = PhaseEngine(
            bounds=DEFAULT_BOUNDS,
            hysteresis=Decimal("0.10"),
            constraints=six_phase_constraints(),
            initial_phase=Phase.FOUR,
        )
        assert engine.current() == Phase.FOUR
