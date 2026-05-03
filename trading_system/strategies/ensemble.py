"""``EnsembleStrategy`` — Phase-6 risk-parity wrapper.

Combines multiple member strategies with inverse-volatility weights
(REQ_SDD_ALG_010) and a global vol-targeting scaler. Each member's
proposals are scaled by ``weight * (vol_target / portfolio_vol)``;
proposals whose scaled size would exceed 1.0 are clamped, and those
that would round to <= 0 are dropped (TradeProposal requires size in
``(0, 1]``).

REQ refs:
- REQ_F_STR_004 — Phase-6 multi-strategy ensemble.
- REQ_SDD_ALG_010 — inverse-volatility (risk-parity) weights with a
  global vol-targeting scaler.
- REQ_SDS_MOD_006 / REQ_SDD_API_001 — read-only over state.
- REQ_SDD_API_005 — stable strategy id.

The portfolio-volatility input is supplied via a callable so the
ensemble does not depend on ``Portfolio`` internals: a Phase-5 caller
can pass realized portfolio vol as an annualized fraction (e.g.,
``Decimal("0.10")`` = 10 %).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from trading_system.models.identifiers import StrategyId
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Money
from trading_system.strategies.protocol import Strategy
from trading_system.strategies.state import MarketState

DEFAULT_STRATEGY_ID = StrategyId("ensemble_v1")


@dataclass(frozen=True, slots=True)
class EnsembleMember:
    """A member strategy and its realized volatility estimate.

    ``realized_vol`` is the annualized realized volatility of the
    member's standalone equity curve (from the strategy registry).
    Higher vol => smaller risk-parity weight.
    """

    strategy: Strategy
    realized_vol: Decimal

    def __post_init__(self) -> None:
        if self.realized_vol <= 0:
            raise ValueError(f"EnsembleMember.realized_vol must be > 0, got {self.realized_vol}")


class EnsembleStrategy:
    """Risk-parity ensemble (REQ_F_STR_004, REQ_SDD_ALG_010)."""

    id: StrategyId

    def __init__(
        self,
        members: list[EnsembleMember],
        *,
        target_vol: Decimal,
        portfolio_vol_provider: Callable[[MarketState], Decimal],
        strategy_id: StrategyId = DEFAULT_STRATEGY_ID,
    ) -> None:
        if not members:
            raise ValueError("EnsembleStrategy requires at least one member")
        if target_vol <= 0:
            raise ValueError(f"EnsembleStrategy.target_vol must be > 0, got {target_vol}")
        self.id = strategy_id
        self._members = list(members)
        self._target_vol = target_vol
        self._portfolio_vol_provider = portfolio_vol_provider

    @property
    def members(self) -> tuple[EnsembleMember, ...]:
        """Read-only view of the member list."""
        return tuple(self._members)

    def risk_parity_weights(self) -> list[Decimal]:
        """Inverse-volatility weights normalized to sum to 1
        (REQ_SDD_ALG_010)."""
        invs = [Decimal(1) / m.realized_vol for m in self._members]
        total = sum(invs, start=Decimal(0))
        return [inv / total for inv in invs]

    def evaluate(self, state: MarketState) -> list[TradeProposal]:
        port_vol = self._portfolio_vol_provider(state)
        # Non-positive vol -> neutral scaler (no infinite scale-down).
        scaler = Decimal(1) if port_vol <= 0 else self._target_vol / port_vol

        weights = self.risk_parity_weights()
        out: list[TradeProposal] = []
        for member, weight in zip(self._members, weights, strict=True):
            factor = weight * scaler
            for proposal in member.strategy.evaluate(state):
                scaled = _scale_proposal(proposal, factor)
                if scaled is not None:
                    out.append(scaled)
        return out


def _scale_proposal(p: TradeProposal, factor: Decimal) -> TradeProposal | None:
    """Return a new TradeProposal with size, expected_net_profit, and
    expected_fees scaled by ``factor``. Drops proposals whose scaled
    size would fall to zero; clamps size at 1.0."""
    if factor <= 0:
        return None
    new_size = min(p.size_pct_of_capital * factor, Decimal(1))
    if new_size <= 0:
        return None
    return TradeProposal(
        instrument=p.instrument,
        side=p.side,
        size_pct_of_capital=new_size,
        expected_net_profit=Money(
            p.expected_net_profit.amount * factor, p.expected_net_profit.currency
        ),
        expected_fees=Money(p.expected_fees.amount * factor, p.expected_fees.currency),
        stop_loss=p.stop_loss,
        source_strategy=p.source_strategy,
    )
