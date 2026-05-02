"""Tests for ``trading_system.models.phase``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.phase import (
    ALLOCATION_TOLERANCE,
    AllocationBucket,
    MarketRegime,
    Phase,
    PhaseConstraints,
)


class TestPhaseEnum:
    def test_six_values(self) -> None:
        assert {p.value for p in Phase} == {1, 2, 3, 4, 5, 6}

    def test_int_compat(self) -> None:
        assert int(Phase.ONE) == 1
        assert int(Phase.SIX) == 6

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            Phase(0)
        with pytest.raises(ValueError):
            Phase(7)


class TestMarketRegimeEnum:
    def test_values(self) -> None:
        assert {r.value for r in MarketRegime} == {"bull", "bear", "sideways", "high_vol"}


class TestAllocationBucket:
    def test_five_values(self) -> None:
        # REQ_SDD_TYP_004: 5-value StrEnum.
        assert {b.value for b in AllocationBucket} == {
            "stock",
            "tactical",
            "structured",
            "turbo",
            "cash",
        }

    def test_distinct_from_instrument_class(self) -> None:
        # AllocationBucket is strategy-allocation, not instrument-class.
        # STOCK and TACTICAL both hold equity instruments but live in
        # different buckets so the budgets stay separate.
        assert AllocationBucket.STOCK.value != AllocationBucket.TACTICAL.value


def make_constraints(**overrides: object) -> PhaseConstraints:
    base: dict[str, object] = {
        "max_positions": 3,
        "max_trades_per_month": 4,
        "allocation_targets": {
            AllocationBucket.STOCK: Decimal("0.90"),
            AllocationBucket.TACTICAL: Decimal("0.10"),
        },
        "turbo_exposure_max": Decimal("0.05"),
        "risk_per_trade_band": (Decimal("0.01"), Decimal("0.02")),
        "max_drawdown": Decimal("0.15"),
        "portfolio_vol_cap": None,
    }
    base.update(overrides)
    return PhaseConstraints(**base)  # type: ignore[arg-type]


class TestPhaseConstraints:
    def test_valid(self) -> None:
        c = make_constraints()
        assert c.max_positions == 3
        assert c.portfolio_vol_cap is None

    def test_with_vol_cap(self) -> None:
        c = make_constraints(portfolio_vol_cap=Decimal("0.12"))
        assert c.portfolio_vol_cap == Decimal("0.12")

    def test_allocation_sums_to_one_within_tolerance(self) -> None:
        # 0.5 + 0.5 - 1e-10 == 0.9999999999, still within tolerance.
        c = make_constraints(
            allocation_targets={
                AllocationBucket.STOCK: Decimal("0.5"),
                AllocationBucket.TACTICAL: Decimal("0.5") - ALLOCATION_TOLERANCE / 2,
            }
        )
        assert c.max_positions == 3  # construction succeeded

    def test_allocation_does_not_sum_to_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="allocation_targets must sum to 1"):
            make_constraints(
                allocation_targets={
                    AllocationBucket.STOCK: Decimal("0.5"),
                    AllocationBucket.TACTICAL: Decimal("0.4"),
                }
            )

    def test_empty_allocation_rejected(self) -> None:
        with pytest.raises(ValueError, match="allocation_targets must be non-empty"):
            make_constraints(allocation_targets={})

    @pytest.mark.parametrize("n", [0, -1])
    def test_non_positive_max_positions_rejected(self, n: int) -> None:
        with pytest.raises(ValueError, match="max_positions"):
            make_constraints(max_positions=n)

    @pytest.mark.parametrize("n", [0, -3])
    def test_non_positive_trades_rejected(self, n: int) -> None:
        with pytest.raises(ValueError, match="max_trades_per_month"):
            make_constraints(max_trades_per_month=n)

    def test_negative_turbo_cap_rejected(self) -> None:
        with pytest.raises(ValueError, match="turbo_exposure_max"):
            make_constraints(turbo_exposure_max=Decimal("-0.01"))

    @pytest.mark.parametrize(
        "band",
        [
            (Decimal(0), Decimal("0.01")),
            (Decimal("-0.01"), Decimal("0.01")),
            (Decimal("0.02"), Decimal("0.01")),  # lo > hi
        ],
    )
    def test_invalid_risk_band_rejected(self, band: tuple[Decimal, Decimal]) -> None:
        with pytest.raises(ValueError, match="risk_per_trade_band"):
            make_constraints(risk_per_trade_band=band)

    @pytest.mark.parametrize("dd", [Decimal(0), Decimal("1.01"), Decimal("-0.1")])
    def test_invalid_drawdown_rejected(self, dd: Decimal) -> None:
        with pytest.raises(ValueError, match="max_drawdown"):
            make_constraints(max_drawdown=dd)

    def test_negative_vol_cap_rejected(self) -> None:
        with pytest.raises(ValueError, match="portfolio_vol_cap"):
            make_constraints(portfolio_vol_cap=Decimal(0))
