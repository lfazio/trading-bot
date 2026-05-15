"""Tests for ``trading_system.portfolio_manager.rebalancer``.

Covers TC_PMG_002 (non-strict band on edge) + TC_PMG_003
(deterministic ordering).

REQ refs: REQ_F_PMG_002, REQ_NF_REP_001, REQ_SDD_PMG_001.
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.models.phase import AllocationBucket
from trading_system.portfolio_manager.proposal import RebalanceProposal
from trading_system.portfolio_manager.rebalancer import Rebalancer


# ---------------------------------------------------------------------------
# TC_PMG_002 — non-strict band on edge
# ---------------------------------------------------------------------------


def test_at_band_edge_is_in_band_no_proposal() -> None:
    """REQ_SDD_PMG_001 — `|drift| <= band` is in-band; no proposal."""
    rebalancer = Rebalancer(drift_band=Decimal("0.05"))
    proposals = rebalancer.propose(
        current_exposure={AllocationBucket.STOCK: Decimal("0.65")},
        target_exposure={AllocationBucket.STOCK: Decimal("0.60")},
    )
    # drift = 0.05 exactly — at the band edge, SKIPPED.
    assert proposals == ()


def test_above_band_emits_decrease_proposal() -> None:
    rebalancer = Rebalancer(drift_band=Decimal("0.05"))
    proposals = rebalancer.propose(
        current_exposure={AllocationBucket.STOCK: Decimal("0.66")},
        target_exposure={AllocationBucket.STOCK: Decimal("0.60")},
    )
    # drift = 0.06 > 0.05 — emit a "decrease" proposal.
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.direction == "decrease"
    assert proposal.drift == Decimal("0.06")


def test_below_band_emits_increase_proposal() -> None:
    rebalancer = Rebalancer(drift_band=Decimal("0.05"))
    proposals = rebalancer.propose(
        current_exposure={AllocationBucket.STOCK: Decimal("0.54")},
        target_exposure={AllocationBucket.STOCK: Decimal("0.60")},
    )
    # drift = -0.06 — magnitude 0.06 > band, emit "increase".
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.direction == "increase"
    assert proposal.drift == Decimal("-0.06")


def test_missing_current_treated_as_zero_drift_check_against_target() -> None:
    """A bucket present in `target_exposure` but missing from
    `current_exposure` defaults to current=0. With target>band, the
    rebalancer emits an "increase" proposal."""
    rebalancer = Rebalancer(drift_band=Decimal("0.05"))
    proposals = rebalancer.propose(
        current_exposure={},
        target_exposure={AllocationBucket.TACTICAL: Decimal("0.20")},
    )
    assert len(proposals) == 1
    assert proposals[0].direction == "increase"
    assert proposals[0].current_pct == Decimal(0)


# ---------------------------------------------------------------------------
# TC_PMG_003 — deterministic ordering
# ---------------------------------------------------------------------------


def test_proposals_sorted_alphabetically_by_bucket_value() -> None:
    rebalancer = Rebalancer(drift_band=Decimal("0.01"))
    proposals = rebalancer.propose(
        current_exposure={
            AllocationBucket.STOCK: Decimal("0.50"),
            AllocationBucket.TURBO: Decimal("0.05"),
            AllocationBucket.STRUCTURED: Decimal("0.20"),
            AllocationBucket.TACTICAL: Decimal("0.15"),
            AllocationBucket.CASH: Decimal("0.10"),
        },
        target_exposure={
            # Targets that make every bucket out-of-band.
            AllocationBucket.STOCK: Decimal("0.30"),
            AllocationBucket.TURBO: Decimal("0.20"),
            AllocationBucket.STRUCTURED: Decimal("0.05"),
            AllocationBucket.TACTICAL: Decimal("0.30"),
            AllocationBucket.CASH: Decimal("0.15"),
        },
    )
    buckets = [p.bucket for p in proposals]
    # AllocationBucket.value alphabetical order:
    # cash < stock < structured < tactical < turbo
    assert buckets == [
        AllocationBucket.CASH,
        AllocationBucket.STOCK,
        AllocationBucket.STRUCTURED,
        AllocationBucket.TACTICAL,
        AllocationBucket.TURBO,
    ]


def test_replay_with_same_inputs_yields_byte_identical_output() -> None:
    rebalancer = Rebalancer()
    args = dict(
        current_exposure={AllocationBucket.STOCK: Decimal("0.66")},
        target_exposure={AllocationBucket.STOCK: Decimal("0.60")},
    )
    a = rebalancer.propose(**args)  # type: ignore[arg-type]
    b = rebalancer.propose(**args)  # type: ignore[arg-type]
    assert a == b
