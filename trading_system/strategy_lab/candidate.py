"""``StrategyCandidate`` — the unit of work passed through the meta-loop.

A candidate carries:
- ``id`` — unique within the cycle.
- ``strategy_factory`` — callable that returns a fresh ``Strategy``
  per backtest run; the loop controller calls it on each window so
  state from a previous run cannot leak.
- ``bucket`` — the AllocationBucket the strategy targets; the
  backtester needs this to call ``Portfolio.apply``.
- ``seed`` — fixed RNG seed for the run; recorded in the
  RegistryEntry so a future replay reproduces metrics exactly
  (REQ_NF_REP_001).
- ``config_hash`` — opaque hash of the candidate's parameters; the
  Generator computes it.
- ``generated_at`` — provenance timestamp.

The loop never trusts a Strategy *instance* to be reusable — the
factory pattern guarantees fresh state per backtest.

REQ refs: REQ_F_MTO_002, REQ_NF_REP_001, REQ_SDS_CRS_003,
REQ_SDD_DAT_004 (registry entry shape).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from trading_system.models.identifiers import StrategyId
from trading_system.models.phase import AllocationBucket
from trading_system.strategies.protocol import Strategy


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    """One candidate strategy under evaluation in a meta-loop cycle."""

    id: StrategyId
    strategy_factory: Callable[[], Strategy]
    bucket: AllocationBucket
    seed: int
    config_hash: str
    generated_at: datetime

    def __post_init__(self) -> None:
        if not self.config_hash:
            raise ValueError("StrategyCandidate.config_hash must be non-empty")
