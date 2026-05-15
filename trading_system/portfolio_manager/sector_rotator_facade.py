"""``SectorRotatorFacade`` — wraps CR-010 ``RotationProposal`` rows
into runtime-shaped ``TradeProposal`` rows.

The CR-010 sector rotator produces sector-weight targets; this facade
turns each (sector, target_weight) pair into per-instrument BUY / SELL
proposals so the existing risk engine consumes them through a single
``TradeProposal`` Protocol.

REQ refs: REQ_F_PMG_003, REQ_F_SCT_007, REQ_SDD_PMG_003.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from trading_system.models.identifiers import StrategyId
from trading_system.models.instrument import Instrument
from trading_system.models.meta import RotationProposal, TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side, StopLoss
from trading_system.portfolio_manager.proposal import Cadence


# Sentinel strategy id used for proposals that originate from the
# sector-rotation facade rather than a registered strategy. The
# Phase-6 runtime wiring may replace this with the rebalancing
# strategy's actual id.
_FACADE_STRATEGY_ID: StrategyId = StrategyId("portfolio_manager.sector_rotator")

# Default per-instrument size when the facade lacks higher-level
# sizing context. v1 ships proposals at this conservative size so
# the risk engine's per-trade band catches anything reckless; the
# Phase-6 sizer will compute account-aware sizes.
_DEFAULT_SIZE_PCT = Decimal("0.01")


@dataclass(slots=True)
class SectorRotatorFacade:
    """Converts ``RotationProposal`` rows into ``TradeProposal`` rows.

    The facade is pure with respect to its inputs; it produces no
    side effects beyond returning a tuple. Empty input returns ``()``
    so phase-1..4 callers handle the no-op uniformly
    (REQ_SDD_PMG_003).
    """

    cadence: Cadence = "quarterly"
    base_currency: Currency = Currency.EUR

    def propose(
        self,
        rotation_proposals: tuple[RotationProposal, ...],
        *,
        instruments_by_sector: Mapping[str, tuple[Instrument, ...]],
        default_stop_loss: StopLoss,
    ) -> tuple[TradeProposal, ...]:
        if not rotation_proposals:
            return ()
        out: list[TradeProposal] = []
        zero = Money(Decimal(0), self.base_currency)
        for rotation in rotation_proposals:
            # Every (sector, target_weight) pair compared against the
            # rotation's recorded source weights. A delta produces
            # one TradeProposal per instrument in that sector.
            for sector, target_weight in rotation.dest_weights.items():
                source_weight = rotation.source_weights.get(sector, Decimal(0))
                if target_weight == source_weight:
                    continue
                side = Side.BUY if target_weight > source_weight else Side.SELL
                for instrument in instruments_by_sector.get(sector, ()):
                    out.append(
                        TradeProposal(
                            instrument=instrument,
                            side=side,
                            size_pct_of_capital=_DEFAULT_SIZE_PCT,
                            # v1 carries zero estimates; the Phase-6
                            # sizer + tax gate fill them in.
                            expected_net_profit=zero,
                            expected_fees=zero,
                            stop_loss=default_stop_loss,
                            source_strategy=_FACADE_STRATEGY_ID,
                        )
                    )
        return tuple(out)
