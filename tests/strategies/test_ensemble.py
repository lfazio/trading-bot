"""Tests for ``trading_system.strategies.ensemble``.

Verifies REQ_F_STR_004 (multi-strategy ensemble) and REQ_SDD_ALG_010
(inverse-volatility weights, vol-targeting scaler).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import StrategyId
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side, StopLoss
from trading_system.strategies.ensemble import (
    EnsembleMember,
    EnsembleStrategy,
    _scale_proposal,
)
from trading_system.strategies.state import MarketState

from .conftest import make_state, make_stock

EUR = Currency.EUR


class FakeStrategy:
    """Minimal Strategy double that returns canned proposals."""

    def __init__(
        self,
        proposals: list[TradeProposal],
        *,
        strategy_id: str = "fake_v1",
    ) -> None:
        self.id = StrategyId(strategy_id)
        self._proposals = proposals

    def evaluate(self, state: MarketState) -> list[TradeProposal]:
        return list(self._proposals)


def make_proposal(size: str = "0.10", strategy_id: str = "fake") -> TradeProposal:
    return TradeProposal(
        instrument=make_stock("ABC"),
        side=Side.BUY,
        size_pct_of_capital=Decimal(size),
        expected_net_profit=Money(Decimal("10"), EUR),
        expected_fees=Money(Decimal("1"), EUR),
        stop_loss=StopLoss(price=Decimal("90")),
        source_strategy=StrategyId(strategy_id),
    )


# ---------------------------------------------------------------------------
# Risk-parity weights
# ---------------------------------------------------------------------------


class TestRiskParityWeights:
    def test_equal_vols_equal_weights(self) -> None:
        a = FakeStrategy([])
        b = FakeStrategy([])
        e = EnsembleStrategy(
            members=[
                EnsembleMember(strategy=a, realized_vol=Decimal("0.10")),
                EnsembleMember(strategy=b, realized_vol=Decimal("0.10")),
            ],
            target_vol=Decimal("0.10"),
            portfolio_vol_provider=lambda _s: Decimal("0.10"),
        )
        weights = e.risk_parity_weights()
        assert weights == [Decimal("0.5"), Decimal("0.5")]

    def test_inverse_vol_ordering(self) -> None:
        # Higher vol -> smaller weight.
        a = FakeStrategy([])
        b = FakeStrategy([])
        e = EnsembleStrategy(
            members=[
                EnsembleMember(strategy=a, realized_vol=Decimal("0.05")),
                EnsembleMember(strategy=b, realized_vol=Decimal("0.20")),
            ],
            target_vol=Decimal("0.10"),
            portfolio_vol_provider=lambda _s: Decimal("0.10"),
        )
        wa, wb = e.risk_parity_weights()
        assert wa > wb
        assert wa + wb == Decimal(1)


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


class TestEnsembleEvaluate:
    def test_scales_proposals_by_weight_and_scaler(self) -> None:
        a = FakeStrategy([make_proposal(size="0.10")])
        e = EnsembleStrategy(
            members=[EnsembleMember(strategy=a, realized_vol=Decimal("0.10"))],
            target_vol=Decimal("0.05"),  # half of port vol -> scaler 0.5
            portfolio_vol_provider=lambda _s: Decimal("0.10"),
        )
        proposals = e.evaluate(make_state())
        assert len(proposals) == 1
        # Weight = 1.0 (single member); scaler = 0.5; original size 0.10.
        # New size = 0.10 * 1.0 * 0.5 = 0.05.
        assert proposals[0].size_pct_of_capital == Decimal("0.05")

    def test_drops_proposal_when_factor_is_zero(self) -> None:
        # Exact zero scaler eliminates the proposal cleanly.
        a = FakeStrategy([make_proposal(size="0.10")])

        # Ensemble can't produce factor=0 directly (target_vol > 0 by
        # construction; vol_provider <= 0 is treated as neutral).
        # Verify the helper directly.
        assert _scale_proposal(make_proposal(size="0.10"), Decimal(0)) is None
        # Sanity: the public evaluate path keeps the proposal when the
        # provider supplies non-positive vol (neutral scaler).
        e = EnsembleStrategy(
            members=[EnsembleMember(strategy=a, realized_vol=Decimal("0.10"))],
            target_vol=Decimal("0.10"),
            portfolio_vol_provider=lambda _s: Decimal(0),
        )
        proposals = e.evaluate(make_state())
        assert len(proposals) == 1

    def test_clamps_size_at_one(self) -> None:
        a = FakeStrategy([make_proposal(size="0.50")])
        e = EnsembleStrategy(
            members=[EnsembleMember(strategy=a, realized_vol=Decimal("0.10"))],
            target_vol=Decimal("1.00"),  # 10x scaler
            portfolio_vol_provider=lambda _s: Decimal("0.10"),
        )
        proposals = e.evaluate(make_state())
        assert len(proposals) == 1
        # 0.50 * 1.0 * 10.0 = 5.0 -> clamped to 1.0.
        assert proposals[0].size_pct_of_capital == Decimal(1)

    def test_combines_member_proposals(self) -> None:
        a = FakeStrategy([make_proposal(size="0.10", strategy_id="a")], strategy_id="a")
        b = FakeStrategy([make_proposal(size="0.20", strategy_id="b")], strategy_id="b")
        e = EnsembleStrategy(
            members=[
                EnsembleMember(strategy=a, realized_vol=Decimal("0.10")),
                EnsembleMember(strategy=b, realized_vol=Decimal("0.10")),
            ],
            target_vol=Decimal("0.10"),
            portfolio_vol_provider=lambda _s: Decimal("0.10"),
        )
        proposals = e.evaluate(make_state())
        assert len(proposals) == 2

    def test_zero_portfolio_vol_neutral_scaler(self) -> None:
        a = FakeStrategy([make_proposal(size="0.10")])
        e = EnsembleStrategy(
            members=[EnsembleMember(strategy=a, realized_vol=Decimal("0.10"))],
            target_vol=Decimal("0.10"),
            portfolio_vol_provider=lambda _s: Decimal(0),
        )
        proposals = e.evaluate(make_state())
        # scaler = 1; weight = 1; original size preserved.
        assert proposals[0].size_pct_of_capital == Decimal("0.10")

    def test_id_is_stable(self) -> None:
        a = FakeStrategy([])
        e = EnsembleStrategy(
            members=[EnsembleMember(strategy=a, realized_vol=Decimal("0.10"))],
            target_vol=Decimal("0.10"),
            portfolio_vol_provider=lambda _s: Decimal("0.10"),
        )
        assert e.id == StrategyId("ensemble_v1")


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


class TestEnsembleConstruction:
    def test_empty_members_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one member"):
            EnsembleStrategy(
                members=[],
                target_vol=Decimal("0.10"),
                portfolio_vol_provider=lambda _s: Decimal("0.10"),
            )

    def test_zero_target_vol_rejected(self) -> None:
        a = FakeStrategy([])
        with pytest.raises(ValueError, match="target_vol must be"):
            EnsembleStrategy(
                members=[EnsembleMember(strategy=a, realized_vol=Decimal("0.10"))],
                target_vol=Decimal(0),
                portfolio_vol_provider=lambda _s: Decimal("0.10"),
            )

    def test_zero_member_vol_rejected(self) -> None:
        a = FakeStrategy([])
        with pytest.raises(ValueError, match="realized_vol"):
            EnsembleMember(strategy=a, realized_vol=Decimal(0))


# Silence unused-import lint when datetime is only used by helper.
_ = datetime
