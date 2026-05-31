"""CR-030 — SRD backtest simulator.

Thin wrapper over ``SRDSettlementScheduler`` that the backtest
engine invokes from its tick loop. Lets operators backtest SRD
strategies against the cached bar series end-to-end without a
live broker.

The simulator does NOT introduce its own determinism story —
``SRDSettlementScheduler.tick(at)`` is deterministic given its
inputs (REQ_NF_SRD_001), and this wrapper just calls it on every
tick. The `Backtest.run()` integration is opt-in: a backtest that
never opens an SRD position SHALL run identically with or without
the simulator attached.

REQ refs:
- REQ_F_SRD_005 (backtest integration) — scheduler runs inside the
  backtest tick loop.
- REQ_NF_SRD_001 — paired-replay determinism extends to the
  backtest engine.
- REQ_SDS_PAP_3_45 — SDS §3.45 simulator shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_system.portfolio.srd_position import SRDSettlement
from trading_system.result import Err, Ok, Result
from trading_system.safety.srd_settlement_scheduler import (
    SRDSettlementScheduler,
)


@dataclass(slots=True)
class SRDSimulator:
    """Compose the SRD settlement scheduler with a settlement-row
    accumulator so a single ``BacktestResult.srd_settlements``
    tuple captures every settlement booked across the run.

    Construction is decoupled from the backtest engine via the
    settlement-scheduler slot so tests inject in-memory fakes.
    The simulator is opt-in — ``Backtest.run()`` constructs one
    only when the wired strategy emits SRD orders OR the
    operator explicitly requests SRD modelling.
    """

    scheduler: SRDSettlementScheduler
    settlements: list[SRDSettlement] = field(default_factory=list)

    def tick(self, at: datetime) -> Result[list[SRDSettlement], str]:
        """Settle every due SRD position at ``at`` + capture the
        resulting rows on this simulator's ledger.

        Returns the scheduler's tick result unchanged. An ``Err``
        propagates back to the backtest's main loop so the run
        aborts deterministically (the Backtest engine catches
        the Err + records it on the result so the operator
        sees the abort)."""
        result = self.scheduler.tick(at)
        if isinstance(result, Ok):
            self.settlements.extend(result.value)
        return result

    def settlements_so_far(self) -> tuple[SRDSettlement, ...]:
        """Snapshot of every settlement booked so far. Used by
        ``BacktestResult`` builders to populate the
        `srd_settlements` field at the end of a run."""
        return tuple(self.settlements)
