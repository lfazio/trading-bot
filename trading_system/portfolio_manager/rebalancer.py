"""``Rebalancer.propose`` — drift-from-target proposal emitter.

Pure function over current exposure + phase targets. Returns one
``RebalanceProposal`` per bucket whose drift strictly exceeds
``drift_band`` (REQ_F_PMG_002 / REQ_SDD_PMG_001 — non-strict on the
band edge; equal-to-band is treated as in-band).

REQ refs: REQ_F_PMG_002, REQ_F_PMG_006, REQ_NF_REP_001,
REQ_SDD_PMG_001.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from trading_system.models.phase import AllocationBucket
from trading_system.portfolio_manager.proposal import (
    Cadence,
    RebalanceProposal,
)


@dataclass(slots=True)
class Rebalancer:
    """Stateless proposal emitter."""

    drift_band: Decimal = Decimal("0.05")
    cadence: Cadence = "monthly"

    def propose(
        self,
        *,
        current_exposure: Mapping[AllocationBucket, Decimal],
        target_exposure: Mapping[AllocationBucket, Decimal],
    ) -> tuple[RebalanceProposal, ...]:
        """Pure: identical inputs ⇒ identical proposal tuple.

        Iteration is sorted by ``AllocationBucket.value`` so the
        returned tuple is deterministic across runs
        (REQ_NF_REP_001 / REQ_SDD_PMG_001). Buckets present in
        ``target_exposure`` but missing from ``current_exposure``
        default to ``Decimal(0)`` current; the rebalancer treats
        them as "fully drifted" candidates if the drift exceeds the
        band.
        """
        proposals: list[RebalanceProposal] = []
        for bucket in sorted(target_exposure, key=lambda b: b.value):
            target = target_exposure[bucket]
            current = current_exposure.get(bucket, Decimal(0))
            drift = current - target
            # Non-strict band — exactly at the edge is treated as
            # in-band per REQ_SDD_PMG_001.
            if abs(drift) <= self.drift_band:
                continue
            direction = "decrease" if drift > 0 else "increase"
            proposals.append(
                RebalanceProposal(
                    bucket=bucket,
                    current_pct=current,
                    target_pct=target,
                    drift=drift,
                    direction=direction,
                    cadence=self.cadence,
                )
            )
        return tuple(proposals)
