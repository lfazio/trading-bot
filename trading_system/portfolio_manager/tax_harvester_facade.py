"""``TaxHarvesterFacade`` — wraps ``HarvestSuggestion`` rows into
SELL ``TradeProposal`` rows.

The facade silently drops suggestions for positions the portfolio no
longer holds (stale suggestions from a prior cycle). Surviving rows
produce SELL proposals carrying the harvest's loss magnitude as the
``expected_net_profit`` (negative — the tax engine treats it as a
loss-side realisation).

REQ refs: REQ_F_PMG_004, REQ_F_TAX_006, REQ_SDD_PMG_002.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from trading_system.models.identifiers import StrategyId
from trading_system.models.instrument import Instrument
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side, StopLoss
from trading_system.portfolio_manager.proposal import Cadence
from trading_system.tax.harvest import HarvestSuggestion


_FACADE_STRATEGY_ID: StrategyId = StrategyId("portfolio_manager.tax_harvester")

_DEFAULT_SIZE_PCT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class HarvestablePosition:
    """The minimal context the facade needs about a held position to
    convert a ``HarvestSuggestion`` into a ``TradeProposal``.

    The Phase-6 runtime wiring will derive these from the live
    ``Portfolio`` view; the facade itself stays decoupled from the
    portfolio types.
    """

    position_id: str
    instrument: Instrument
    stop_loss: StopLoss


@dataclass(slots=True)
class TaxHarvesterFacade:
    """Convert harvest suggestions into SELL trade proposals."""

    cadence: Cadence = "monthly"
    base_currency: Currency = Currency.EUR

    def propose(
        self,
        harvest_suggestions: tuple[HarvestSuggestion, ...],
        *,
        held_positions: Mapping[str, HarvestablePosition],
    ) -> tuple[TradeProposal, ...]:
        """Pure function. Silently drops suggestions for non-held
        positions (REQ_SDD_PMG_002 — stale-suggestion path)."""
        out: list[TradeProposal] = []
        zero = Money(Decimal(0), self.base_currency)
        for suggestion in harvest_suggestions:
            if suggestion.position_id not in held_positions:
                # Stale suggestion (position closed in a prior tick);
                # silently drop. Operators see the original
                # HarvestSuggestion in the audit log; the facade
                # doesn't surface it.
                continue
            held = held_positions[suggestion.position_id]
            # The harvest produces a negative expected_net_profit
            # (we are *realising* a loss). The tax engine's
            # `expected_net_profit > 5 × fees AFTER TAX` gate is
            # short-circuited by the tax-loss harvester's own
            # admission rules; v1 ships the proposal shape and
            # leaves the gate-bypass plumbing to the Phase-6
            # runtime wiring.
            expected_loss = Money(
                -suggestion.loss_magnitude.amount,
                suggestion.loss_magnitude.currency,
            )
            out.append(
                TradeProposal(
                    instrument=held.instrument,
                    side=Side.SELL,
                    size_pct_of_capital=_DEFAULT_SIZE_PCT,
                    expected_net_profit=expected_loss,
                    expected_fees=zero,
                    stop_loss=held.stop_loss,
                    source_strategy=_FACADE_STRATEGY_ID,
                )
            )
        return tuple(out)
