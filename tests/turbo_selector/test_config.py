"""Tests for ``trading_system.turbo_selector.config``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.turbo_selector.config import TurboSelectorConfig


class TestTurboSelectorConfig:
    def test_defaults(self) -> None:
        cfg = TurboSelectorConfig()
        assert cfg.knockout_min_distance == Decimal("0.05")
        assert cfg.spread_max == Decimal("0.015")
        assert cfg.weights == (
            Decimal("0.35"),
            Decimal("0.25"),
            Decimal("0.20"),
            Decimal("0.20"),
        )
        assert cfg.threshold == Decimal("0.50")

    @pytest.mark.parametrize(
        "kwargs, msg",
        [
            ({"knockout_min_distance": Decimal("-0.01")}, "knockout_min_distance"),
            ({"knockout_min_distance": Decimal("1.5")}, "knockout_min_distance"),
            ({"spread_max": Decimal("-0.01")}, "spread_max"),
            ({"max_volatility": Decimal("1.5")}, "max_volatility"),
            ({"threshold": Decimal("1.5")}, "threshold"),
            ({"min_liquidity": Decimal("-1")}, "min_liquidity"),
            ({"leverage_efficiency_reference": Decimal(0)}, "leverage_efficiency_reference"),
            ({"knockout_sigmoid_k": Decimal(0)}, "knockout_sigmoid_k"),
            ({"vol_window": 0}, "vol_window"),
            ({"volume_window": -1}, "volume_window"),
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, object], msg: str) -> None:
        with pytest.raises(ValueError, match=msg):
            TurboSelectorConfig(**kwargs)

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValueError, match="weights must all be"):
            TurboSelectorConfig(
                weights=(Decimal("-0.1"), Decimal("0.4"), Decimal("0.4"), Decimal("0.3"))
            )

    def test_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match="weights must sum to 1"):
            TurboSelectorConfig(
                weights=(Decimal("0.30"), Decimal("0.20"), Decimal("0.20"), Decimal("0.20"))
            )

    def test_frozen(self) -> None:
        cfg = TurboSelectorConfig()
        with pytest.raises(AttributeError):
            cfg.threshold = Decimal("0.30")  # type: ignore[misc]
