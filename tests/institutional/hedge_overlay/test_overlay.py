"""TC_HOV_005 (phase gating) + TC_HOV_006 (sizing math) + TC_HOV_007
(determinism).

REQ refs:
- REQ_F_HOV_003 — at most one proposal per call; band check;
  notional clamp.
- REQ_SDS_HOV_002 — phase gate FIRST.
- REQ_SDD_HOV_002 — phase gate before reading any other input.
- REQ_NF_HOV_001 — replay determinism.
- REQ_F_CAP_011 — hard 10 % overlay cap.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import product

from trading_system.institutional.hedge_overlay import (
    HedgeOverlay,
    OverlayPolicy,
)
from trading_system.result import Ok


# ---------------------------------------------------------------------------
# TC_HOV_005 — phase gating property test
# ---------------------------------------------------------------------------


def test_phases_1_to_5_always_bypass() -> None:
    """REQ_SDS_HOV_002 — phase < 6 ⇒ ``Ok(())`` over every input
    combination."""
    overlay = HedgeOverlay()
    policy = OverlayPolicy()
    phases = (1, 2, 3, 4, 5)
    betas = (Decimal("0"), Decimal("0.5"), Decimal("2.0"))
    equities = (Decimal("10000"), Decimal("1000000"))
    for phase, beta, eq in product(phases, betas, equities):
        res = overlay.size(
            current_beta=beta, policy=policy, phase=phase, household_equity=eq
        )
        assert res == Ok(()), (
            f"phase={phase} beta={beta} equity={eq} did NOT bypass — "
            "REQ_SDS_HOV_002 phase-gate-first violated"
        )


def test_phase_6_in_band_returns_empty() -> None:
    """REQ_F_HOV_003 — beta within the band ⇒ no proposal."""
    overlay = HedgeOverlay()
    policy = OverlayPolicy()  # beta_band=0.05; target_beta=0.5
    res = overlay.size(
        current_beta=Decimal("0.52"),
        policy=policy,
        phase=6,
        household_equity=Decimal("1000000"),
    )
    assert res == Ok(())


def test_phase_6_at_band_boundary_returns_empty() -> None:
    overlay = HedgeOverlay()
    policy = OverlayPolicy()
    # beta_delta = exactly 0.05 ⇒ in-band per `<=` predicate.
    res = overlay.size(
        current_beta=Decimal("0.55"),
        policy=policy,
        phase=6,
        household_equity=Decimal("1000000"),
    )
    assert res == Ok(())


# ---------------------------------------------------------------------------
# TC_HOV_006 — sizing math byte-exact example
# ---------------------------------------------------------------------------


def test_phase_6_out_of_band_emits_short_proposal_at_cap() -> None:
    """current_beta=1.5 ⇒ raw_notional = 1.0 × 1M × 1.0 = 1_000_000,
    clamped to cap 0.10 × 1M = 100_000. Side = short."""
    overlay = HedgeOverlay()
    policy = OverlayPolicy()
    res = overlay.size(
        current_beta=Decimal("1.5"),
        policy=policy,
        phase=6,
        household_equity=Decimal("1000000"),
    )
    proposals = res.unwrap()
    assert len(proposals) == 1
    p = proposals[0]
    assert p.side == "short"
    assert p.notional == Decimal("100000")
    assert p.target_beta_delta == Decimal("1.0")
    assert p.cadence == "weekly"
    assert p.benchmark == "EUROSTOXX50"


def test_phase_6_below_target_emits_long_proposal() -> None:
    """current_beta=0.1 ⇒ beta_delta=-0.4 ⇒ side=long."""
    overlay = HedgeOverlay()
    policy = OverlayPolicy()
    res = overlay.size(
        current_beta=Decimal("0.1"),
        policy=policy,
        phase=6,
        household_equity=Decimal("1000000"),
    )
    proposals = res.unwrap()
    assert len(proposals) == 1
    assert proposals[0].side == "long"
    assert proposals[0].target_beta_delta == Decimal("-0.4")


def test_raw_notional_below_cap_emits_unclamped() -> None:
    """current_beta=0.6 (delta=0.1) ⇒ raw_notional = 0.1 × 1M × 1.0 =
    100_000; cap = 0.10 × 1M = 100_000 ⇒ notional = 100_000 (no clamp
    needed). Beta delta 0.1 > beta_band 0.05 so a proposal is emitted."""
    overlay = HedgeOverlay()
    policy = OverlayPolicy()
    res = overlay.size(
        current_beta=Decimal("0.6"),
        policy=policy,
        phase=6,
        household_equity=Decimal("1000000"),
    )
    proposals = res.unwrap()
    assert proposals[0].notional == Decimal("100000")


# ---------------------------------------------------------------------------
# TC_HOV_007 — determinism
# ---------------------------------------------------------------------------


def test_size_is_deterministic() -> None:
    overlay = HedgeOverlay()
    policy = OverlayPolicy()
    kwargs = {
        "current_beta": Decimal("1.5"),
        "policy": policy,
        "phase": 6,
        "household_equity": Decimal("1000000"),
    }
    a = overlay.size(**kwargs)
    b = overlay.size(**kwargs)
    assert a == b


def test_size_diverges_when_beta_changes() -> None:
    overlay = HedgeOverlay()
    policy = OverlayPolicy()
    a = overlay.size(
        current_beta=Decimal("1.5"),
        policy=policy,
        phase=6,
        household_equity=Decimal("1000000"),
    )
    b = overlay.size(
        current_beta=Decimal("1.51"),
        policy=policy,
        phase=6,
        household_equity=Decimal("1000000"),
    )
    assert a != b
