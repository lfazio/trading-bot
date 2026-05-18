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
    """One candidate strategy under evaluation in a meta-loop cycle.

    ``hypothesis_ids`` (CR-002 Phase B — REQ_F_QNT_005) — every
    candidate generated from a VALIDATED Hypothesis carries its
    source-hypothesis ids here so the cycle's ``ImprovementReport``
    can pin "this strategy traces back to hypothesis X, Y" without
    a separate lookup. v1 hypothesis-naive generators leave the
    tuple empty; Phase-B generator_v2 (CR-002 follow-up) populates
    it. Sorted + de-duplicated; the constructor enforces the
    invariant for byte-identical replay (REQ_NF_QNT_002 family).
    """

    id: StrategyId
    strategy_factory: Callable[[], Strategy]
    bucket: AllocationBucket
    seed: int
    config_hash: str
    generated_at: datetime
    hypothesis_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.config_hash:
            raise ValueError("StrategyCandidate.config_hash must be non-empty")
        if self.hypothesis_ids:
            for hid in self.hypothesis_ids:
                if not str(hid).strip():
                    raise ValueError(
                        "StrategyCandidate.hypothesis_ids entries must be non-empty"
                    )
            if len(set(self.hypothesis_ids)) != len(self.hypothesis_ids):
                raise ValueError(
                    "StrategyCandidate.hypothesis_ids must be unique; "
                    f"got duplicates in {self.hypothesis_ids}"
                )
            if list(self.hypothesis_ids) != sorted(self.hypothesis_ids):
                raise ValueError(
                    "StrategyCandidate.hypothesis_ids must be sorted "
                    f"lexicographically; got {self.hypothesis_ids}"
                )
