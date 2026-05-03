"""Tests for ``trading_system.turbo_selector.score``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Turbo
from trading_system.models.money import Currency
from trading_system.turbo_selector.config import TurboSelectorConfig
from trading_system.turbo_selector.score import (
    cost_score,
    expected_move_capture_score,
    knockout_distance_score,
    leverage_efficiency_score,
)

EUR = Currency.EUR


def make_turbo(
    *,
    leverage: str = "5",
    knockout: str = "90",
    spread_pct: str = "0.005",
) -> Turbo:
    return Turbo(
        id=InstrumentId("t-1"),
        symbol="T1",
        exchange="EPA",
        currency=EUR,
        cls=InstrumentClass.TURBO,
        underlying=InstrumentId("AAPL"),
        direction="LONG",
        leverage=Decimal(leverage),
        knockout=Decimal(knockout),
        spread_pct=Decimal(spread_pct),
    )


# ---------------------------------------------------------------------------
# knockout_distance_score (REQ_SDD_ALG_011)
# ---------------------------------------------------------------------------


class TestKnockoutDistanceScore:
    def test_at_threshold_is_half(self) -> None:
        # underlying 100, knockout 95 -> distance 0.05 == threshold.
        # Sigmoid centred at threshold => 0.5 exactly.
        cfg = TurboSelectorConfig()
        score = knockout_distance_score(make_turbo(knockout="95"), Decimal("100"), cfg)
        assert score == Decimal("0.5")

    def test_far_from_knockout_high_score(self) -> None:
        cfg = TurboSelectorConfig()
        score = knockout_distance_score(make_turbo(knockout="50"), Decimal("100"), cfg)
        assert score > Decimal("0.99")

    def test_close_to_knockout_low_score(self) -> None:
        cfg = TurboSelectorConfig()
        # underlying 100, knockout 99 -> distance 0.01 < threshold 0.05
        score = knockout_distance_score(make_turbo(knockout="99"), Decimal("100"), cfg)
        assert score < Decimal("0.20")

    def test_zero_or_negative_underlying_returns_zero(self) -> None:
        cfg = TurboSelectorConfig()
        assert knockout_distance_score(make_turbo(), Decimal(0), cfg) == Decimal(0)


# ---------------------------------------------------------------------------
# leverage_efficiency_score
# ---------------------------------------------------------------------------


class TestLeverageEfficiencyScore:
    def test_zero_leverage_zero(self) -> None:
        # Turbo construction requires leverage > 1 so just test the
        # formula directly with a mocked-low value via the cfg.
        cfg = TurboSelectorConfig(leverage_efficiency_reference=Decimal(20))
        score = leverage_efficiency_score(make_turbo(leverage="2"), cfg)
        assert score == Decimal("0.1")

    def test_at_reference_one(self) -> None:
        cfg = TurboSelectorConfig(leverage_efficiency_reference=Decimal(20))
        score = leverage_efficiency_score(make_turbo(leverage="20"), cfg)
        assert score == Decimal(1)

    def test_above_reference_clamped_to_one(self) -> None:
        cfg = TurboSelectorConfig(leverage_efficiency_reference=Decimal(20))
        score = leverage_efficiency_score(make_turbo(leverage="50"), cfg)
        assert score == Decimal(1)


# ---------------------------------------------------------------------------
# cost_score
# ---------------------------------------------------------------------------


class TestCostScore:
    def test_zero_spread_one(self) -> None:
        cfg = TurboSelectorConfig()
        score = cost_score(make_turbo(spread_pct="0"), cfg)
        assert score == Decimal(1)

    def test_at_max_zero(self) -> None:
        cfg = TurboSelectorConfig()
        score = cost_score(make_turbo(spread_pct="0.015"), cfg)
        assert score == Decimal(0)

    def test_above_max_clamped_to_zero(self) -> None:
        cfg = TurboSelectorConfig()
        score = cost_score(make_turbo(spread_pct="0.030"), cfg)
        assert score == Decimal(0)

    def test_zero_spread_max_cfg_returns_zero(self) -> None:
        cfg = TurboSelectorConfig(spread_max=Decimal(0))
        score = cost_score(make_turbo(spread_pct="0"), cfg)
        assert score == Decimal(0)


# ---------------------------------------------------------------------------
# expected_move_capture_score
# ---------------------------------------------------------------------------


class TestExpectedMoveCaptureScore:
    def test_zero_vol_zero(self) -> None:
        cfg = TurboSelectorConfig()
        score = expected_move_capture_score(make_turbo(), Decimal(0), cfg)
        assert score == Decimal(0)

    def test_high_leverage_high_vol_clamps(self) -> None:
        cfg = TurboSelectorConfig()
        score = expected_move_capture_score(make_turbo(leverage="10"), Decimal("0.50"), cfg)
        assert score == Decimal(1)

    def test_proportional(self) -> None:
        cfg = TurboSelectorConfig()
        # leverage 5 * vol 0.05 / max_vol 0.50 = 0.5
        score = expected_move_capture_score(make_turbo(leverage="5"), Decimal("0.05"), cfg)
        assert score == Decimal("0.5")


# ---------------------------------------------------------------------------
# Score component value range — every helper stays in [0, 1]
# ---------------------------------------------------------------------------


class TestComponentRange:
    @pytest.mark.parametrize("price", [Decimal("50"), Decimal("100"), Decimal("200")])
    @pytest.mark.parametrize("knockout", [Decimal("40"), Decimal("90"), Decimal("99")])
    def test_knockout_distance_clamped(self, price: Decimal, knockout: Decimal) -> None:
        cfg = TurboSelectorConfig()
        s = knockout_distance_score(make_turbo(knockout=str(knockout)), price, cfg)
        assert Decimal(0) <= s <= Decimal(1)
