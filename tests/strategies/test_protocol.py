"""Tests for ``trading_system.strategies.protocol`` + ``state``.

Verifies REQ_SDD_API_002 (runtime-checkable Protocol) for both
``Strategy`` and ``PortfolioView``, REQ_SDS_MOD_006 (frozen state),
and REQ_SDD_API_001 (read-only over state).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.strategies.core import CoreStrategy, CoreStrategyConfig
from trading_system.strategies.ensemble import EnsembleMember, EnsembleStrategy
from trading_system.strategies.protocol import PortfolioView, Strategy
from trading_system.strategies.tactical import (
    TacticalStrategy,
    TacticalStrategyConfig,
)
from trading_system.tax.config import TaxConfig

from .conftest import (
    StubPortfolioView,
    make_fee_model,
    make_state,
)


class TestPortfolioViewProtocol:
    def test_stub_satisfies_protocol(self) -> None:
        assert isinstance(StubPortfolioView(), PortfolioView)


class TestStrategyProtocol:
    def test_core_strategy_satisfies_protocol(self) -> None:
        s = CoreStrategy(CoreStrategyConfig(), make_fee_model(), TaxConfig.default())
        assert isinstance(s, Strategy)

    def test_tactical_strategy_satisfies_protocol(self) -> None:
        s = TacticalStrategy(TacticalStrategyConfig(), make_fee_model(), TaxConfig.default())
        assert isinstance(s, Strategy)

    def test_ensemble_strategy_satisfies_protocol(self) -> None:
        member = CoreStrategy(CoreStrategyConfig(), make_fee_model(), TaxConfig.default())
        e = EnsembleStrategy(
            members=[EnsembleMember(strategy=member, realized_vol=Decimal("0.10"))],
            target_vol=Decimal("0.10"),
            portfolio_vol_provider=lambda _state: Decimal("0.10"),
        )
        assert isinstance(e, Strategy)


class TestMarketStateFrozen:
    def test_state_is_frozen(self) -> None:
        state = make_state()
        with pytest.raises((AttributeError, TypeError)):
            # Frozen dataclass: assignment raises.
            state.at = datetime(2027, 1, 1)  # type: ignore[misc]

    def test_screener_ranking_is_a_tuple(self) -> None:
        state = make_state()
        assert isinstance(state.screener_ranking, tuple)
