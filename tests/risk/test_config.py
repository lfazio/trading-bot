"""Tests for ``trading_system.risk.config``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.instrument import InstrumentClass
from trading_system.models.phase import MarketRegime
from trading_system.risk.config import RiskConfig


class TestRiskConfig:
    def test_defaults(self) -> None:
        cfg = RiskConfig()
        assert cfg.single_asset_cap == Decimal("0.30")
        assert cfg.correlation_max == Decimal("0.85")
        assert cfg.correlation_window_days == 60
        assert cfg.regimes_forbidden_for(InstrumentClass.STRUCTURED) == (
            MarketRegime.HIGH_VOL,
            MarketRegime.BEAR,
        )
        assert cfg.regimes_forbidden_for(InstrumentClass.TURBO) == (MarketRegime.HIGH_VOL,)

    def test_unknown_class_returns_empty(self) -> None:
        cfg = RiskConfig()
        assert cfg.regimes_forbidden_for(InstrumentClass.STOCK) == ()

    @pytest.mark.parametrize(
        "kwargs, msg",
        [
            ({"single_asset_cap": Decimal(0)}, "single_asset_cap"),
            ({"single_asset_cap": Decimal("1.5")}, "single_asset_cap"),
            ({"correlation_max": Decimal("1.5")}, "correlation_max"),
            ({"correlation_max": Decimal("-0.1")}, "correlation_max"),
            ({"correlation_window_days": 0}, "correlation_window_days"),
            ({"correlation_window_days": -1}, "correlation_window_days"),
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, object], msg: str) -> None:
        with pytest.raises(ValueError, match=msg):
            RiskConfig(**kwargs)

    def test_frozen(self) -> None:
        cfg = RiskConfig()
        with pytest.raises(AttributeError):
            cfg.single_asset_cap = Decimal("0.5")  # type: ignore[misc]
