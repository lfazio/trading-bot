"""Phase-6 drills — vol-target tracking, risk-parity stability,
ensemble decorrelation.

EnsembleStrategy is the Phase-6 risk-parity wrapper that:
1. Computes inverse-volatility weights across members
   (REQ_SDD_ALG_010).
2. Scales the weighted proposals by ``target_vol / portfolio_vol``
   to track a documented portfolio volatility target.
3. Produces a combined proposal stream whose theoretical
   portfolio volatility (under decorrelated members) is lower
   than the equal-weighted baseline.

This drill verifies all three properties end-to-end against the
shipped ``EnsembleStrategy``. The members are stubbed (each emits
one fixed proposal on every ``evaluate`` call) so the drill stays
focused on the ensemble's scaling math; the per-strategy signal
logic is covered by the per-module tests.

REQ refs:
- REQ_F_STR_004 — Phase-6 multi-strategy ensemble.
- REQ_SDD_ALG_010 — inverse-volatility weights + global vol-
  targeting scaler.
- REQ_NF_REP_001 — replay determinism (same inputs ⇒ same
  output).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest

from trading_system.models.identifiers import (
    InstrumentId,
    StrategyId,
)
from trading_system.models.instrument import Instrument, InstrumentClass
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side, StopLoss
from trading_system.strategies.ensemble import (
    EnsembleMember,
    EnsembleStrategy,
)


_EUR = Currency.EUR
_INSTR = Instrument(
    id=InstrumentId("ASML.AS"),
    symbol="ASML",
    exchange="AS",
    currency=_EUR,
    cls=InstrumentClass.STOCK,
)


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=_EUR)


# ---------------------------------------------------------------------------
# Stub strategy — emits one fixed proposal per evaluate call
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FixedProposalStrategy:
    """Emits a single ``TradeProposal`` of the documented shape on
    every ``evaluate`` call. The ensemble scales the proposal
    deterministically, so this stub is enough to test the scaling
    math without spinning up a Backtest."""

    id: StrategyId
    base_size_pct: Decimal = Decimal("0.02")

    def evaluate(self, state: Any) -> list[TradeProposal]:
        del state
        return [
            TradeProposal(
                instrument=_INSTR,
                side=Side.BUY,
                size_pct_of_capital=self.base_size_pct,
                expected_net_profit=_eur("100.00"),
                expected_fees=_eur("1.00"),
                stop_loss=StopLoss(price=Decimal("40")),
                source_strategy=self.id,
            )
        ]


def _ensemble(
    vols: list[Decimal],
    *,
    target_vol: Decimal,
    portfolio_vol: Decimal,
    base_sizes: list[Decimal] | None = None,
) -> EnsembleStrategy:
    """Helper — build an EnsembleStrategy with N fixed-proposal
    members at the documented vol levels."""
    sizes = base_sizes or [Decimal("0.02")] * len(vols)
    assert len(sizes) == len(vols)
    members = [
        EnsembleMember(
            strategy=_FixedProposalStrategy(
                id=StrategyId(f"member-{i}"), base_size_pct=size
            ),
            realized_vol=vol,
        )
        for i, (vol, size) in enumerate(zip(vols, sizes, strict=True))
    ]
    return EnsembleStrategy(
        members=members,
        target_vol=target_vol,
        portfolio_vol_provider=lambda _state: portfolio_vol,
    )


# ===========================================================================
# Scenario 1 — vol-target scaler math
# ===========================================================================


def test_drill_scaler_neutral_when_portfolio_vol_equals_target() -> None:
    """REQ_SDD_ALG_010 — when portfolio_vol == target_vol, the
    scaler is 1.0; proposals retain their original size after
    the inverse-vol weighting."""
    ensemble = _ensemble(
        vols=[Decimal("0.10"), Decimal("0.10")],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.10"),
    )
    proposals = ensemble.evaluate(None)  # type: ignore[arg-type]
    # Two members, equal vol ⇒ weights = 0.5 each. Scaler = 1.
    # Each proposal's size = base × weight × scaler = 0.02 × 0.5 = 0.01
    assert len(proposals) == 2
    for p in proposals:
        assert p.size_pct_of_capital == Decimal("0.01")


def test_drill_scaler_halves_when_portfolio_vol_doubles_target() -> None:
    """REQ_SDD_ALG_010 — portfolio_vol = 2 × target_vol ⇒
    scaler = 0.5 ⇒ proposals halved relative to neutral."""
    ensemble = _ensemble(
        vols=[Decimal("0.10"), Decimal("0.10")],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.20"),  # 2× target
    )
    proposals = ensemble.evaluate(None)  # type: ignore[arg-type]
    # base × weight × scaler = 0.02 × 0.5 × 0.5 = 0.005
    for p in proposals:
        assert p.size_pct_of_capital == Decimal("0.005")


def test_drill_scaler_doubles_when_portfolio_vol_halves_target() -> None:
    """REQ_SDD_ALG_010 — portfolio_vol = 0.5 × target_vol ⇒
    scaler = 2.0 ⇒ proposals doubled relative to neutral."""
    ensemble = _ensemble(
        vols=[Decimal("0.10"), Decimal("0.10")],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.05"),
    )
    proposals = ensemble.evaluate(None)  # type: ignore[arg-type]
    # base × weight × scaler = 0.02 × 0.5 × 2 = 0.02
    for p in proposals:
        assert p.size_pct_of_capital == Decimal("0.02")


def test_drill_scaler_clamps_at_unit_size() -> None:
    """REQ_SDD_ALG_010 — scaled size > 1.0 SHALL be clamped at
    1.0 (TradeProposal invariant: size in (0, 1]). The ensemble
    won't propose a >100 % position regardless of how lopsided
    the vol-target scaler gets."""
    # Single member with base size 0.50; portfolio_vol = 0.01,
    # target_vol = 0.10 ⇒ scaler = 10 ⇒ raw scaled size = 5.0 ⇒
    # clamped to 1.0.
    ensemble = _ensemble(
        vols=[Decimal("0.10")],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.01"),
        base_sizes=[Decimal("0.50")],
    )
    proposals = ensemble.evaluate(None)  # type: ignore[arg-type]
    assert len(proposals) == 1
    assert proposals[0].size_pct_of_capital == Decimal("1.0")


def test_drill_non_positive_portfolio_vol_is_neutral() -> None:
    """REQ_SDD_ALG_010 — zero or negative portfolio_vol input
    SHALL produce a neutral (scaler=1) outcome rather than a
    division-by-zero / negative-scaler. The vol provider can
    plausibly emit 0 on a brand-new backtest with no equity
    history yet."""
    ensemble = _ensemble(
        vols=[Decimal("0.10")],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0"),
    )
    proposals = ensemble.evaluate(None)  # type: ignore[arg-type]
    # weight = 1.0; scaler = 1 ⇒ size unchanged from base.
    assert proposals[0].size_pct_of_capital == Decimal("0.02")


# ===========================================================================
# Scenario 2 — risk-parity weight stability under perturbation
# ===========================================================================


def test_drill_weights_track_inverse_vol() -> None:
    """REQ_SDD_ALG_010 — weights are inverse-vol-normalised.
    Member with vol = V_i has weight (1/V_i) / sum(1/V_j).
    Two members with vols 0.10 and 0.20 ⇒ weights 2/3 and 1/3."""
    ensemble = _ensemble(
        vols=[Decimal("0.10"), Decimal("0.20")],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.10"),
    )
    weights = ensemble.risk_parity_weights()
    # 1/0.10 = 10; 1/0.20 = 5; total = 15
    # weight_a = 10/15 = 0.666...; weight_b = 5/15 = 0.333...
    assert weights[0] == Decimal("10") / Decimal("15")
    assert weights[1] == Decimal("5") / Decimal("15")


def test_drill_weights_stable_under_small_vol_perturbation() -> None:
    """REQ_SDD_ALG_010 — small (5 %) perturbations in member vol
    SHALL produce small (≲ 5 %) changes in weights. No member's
    weight SHALL flip by an order of magnitude under a small
    input change.

    Baseline vols [0.10, 0.12, 0.15, 0.20] ⇒ baseline weights.
    Perturb member 0's vol +5 % (0.10 → 0.105). Recompute.
    Assert each weight stays within ±10 % of baseline.
    """
    baseline = _ensemble(
        vols=[
            Decimal("0.10"),
            Decimal("0.12"),
            Decimal("0.15"),
            Decimal("0.20"),
        ],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.10"),
    )
    perturbed = _ensemble(
        vols=[
            Decimal("0.105"),  # +5 %
            Decimal("0.12"),
            Decimal("0.15"),
            Decimal("0.20"),
        ],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.10"),
    )
    w_base = baseline.risk_parity_weights()
    w_pert = perturbed.risk_parity_weights()
    for i, (b, p) in enumerate(zip(w_base, w_pert, strict=True)):
        # |delta| / baseline ≤ 0.10
        rel_change = abs(p - b) / b
        assert rel_change <= Decimal("0.10"), (
            f"member {i}: weight changed from {b} to {p} "
            f"(rel {rel_change:.4f}) — perturbation propagated too far"
        )


def test_drill_weights_sum_to_one_exactly() -> None:
    """REQ_SDD_ALG_010 — weights normalise to sum = 1 (within
    Decimal arithmetic precision)."""
    ensemble = _ensemble(
        vols=[Decimal(str(v)) for v in (0.07, 0.09, 0.12, 0.16, 0.22)],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.10"),
    )
    weights = ensemble.risk_parity_weights()
    total = sum(weights, start=Decimal(0))
    # Tight tolerance — Decimal divisions can leave a tiny residue
    # in the 20+ decimal place range, but well under 1e-15.
    assert abs(total - Decimal(1)) < Decimal("1e-15")


def test_drill_weights_deterministic_across_recompute() -> None:
    """REQ_NF_REP_001 — calling ``risk_parity_weights`` twice on
    the same EnsembleStrategy SHALL return equal lists."""
    ensemble = _ensemble(
        vols=[Decimal("0.10"), Decimal("0.12"), Decimal("0.15")],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.10"),
    )
    w1 = ensemble.risk_parity_weights()
    w2 = ensemble.risk_parity_weights()
    assert w1 == w2


# ===========================================================================
# Scenario 3 — ensemble decorrelation effect
# ===========================================================================


def test_drill_low_vol_member_gets_largest_weight() -> None:
    """REQ_SDD_ALG_010 — risk-parity preference: low-vol
    members get more capital weight than high-vol members.
    This is the documented "decorrelation by risk budget"
    behaviour — concentration into stable signals reduces
    portfolio swings."""
    ensemble = _ensemble(
        vols=[
            Decimal("0.05"),  # low-vol
            Decimal("0.10"),
            Decimal("0.20"),  # high-vol
        ],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.10"),
    )
    weights = ensemble.risk_parity_weights()
    # Largest weight on the low-vol member.
    assert weights[0] > weights[1] > weights[2]


def test_drill_inverse_vol_portfolio_has_lower_theoretical_variance() -> None:
    """REQ_F_STR_004 — under the uncorrelated-members assumption
    (the limiting case for "ensemble decorrelation"), portfolio
    variance is sum(w_i² × σ_i²). Risk-parity weights produce a
    LOWER theoretical variance than equal-weight weights for
    disparate member vols.

    Setup: members with vols [0.05, 0.10, 0.20].
    - Equal weight (1/3 each):
        sum((1/3)² × σ²) = (1/9) × (0.0025 + 0.01 + 0.04) = 0.005833...
    - Risk-parity weights ∝ [1/0.05, 1/0.10, 1/0.20] = [20, 10, 5]
      ⇒ normalised [4/7, 2/7, 1/7]:
        4²/49 × 0.0025 + 2²/49 × 0.01 + 1²/49 × 0.04 = ?
        (16 × 0.0025 + 4 × 0.01 + 1 × 0.04) / 49
        = (0.04 + 0.04 + 0.04) / 49
        = 0.12 / 49
        ≈ 0.002449
    - Risk-parity variance ≈ 0.002449 vs equal-weight ≈ 0.005833 —
      risk parity wins by ~58 %.

    Asserts the risk-parity variance is strictly less than the
    equal-weight variance for the documented setup.
    """
    vols = [Decimal("0.05"), Decimal("0.10"), Decimal("0.20")]
    ensemble = _ensemble(
        vols=vols,
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.10"),
    )
    weights_rp = ensemble.risk_parity_weights()
    n = Decimal(len(vols))

    # Theoretical (uncorrelated) portfolio variance = Σ wᵢ² σᵢ².
    def _portfolio_var(weights: list[Decimal]) -> Decimal:
        return sum(
            (w * w * v * v for w, v in zip(weights, vols, strict=True)),
            start=Decimal(0),
        )

    rp_var = _portfolio_var(weights_rp)
    eq_var = _portfolio_var([Decimal(1) / n] * len(vols))
    assert rp_var < eq_var, (
        f"risk-parity variance {rp_var} NOT < equal-weight {eq_var}"
    )


def test_drill_combined_proposals_carry_each_member_share() -> None:
    """REQ_F_STR_004 — the ensemble's output combines the
    weighted proposals from EVERY member that emitted one.
    The total ``size_pct`` across the ensemble's output sums to
    base_size × scaler (the weights sum to 1, so the weighted
    sum collapses to the original size × scaler when every
    member emits an identical proposal)."""
    base_size = Decimal("0.04")
    n_members = 4
    ensemble = _ensemble(
        vols=[Decimal("0.10")] * n_members,
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.10"),
        base_sizes=[base_size] * n_members,
    )
    proposals = ensemble.evaluate(None)  # type: ignore[arg-type]
    total_size = sum(
        (p.size_pct_of_capital for p in proposals), start=Decimal(0)
    )
    # Σ (w_i × base_size × scaler) = base_size × scaler × Σ w_i
    # = base_size × 1 × 1 = base_size.
    assert total_size == base_size


# ===========================================================================
# Scenario 4 — proposal-scaling math is byte-deterministic
# ===========================================================================


def test_drill_ensemble_evaluate_is_deterministic(_runner=None) -> None:  # type: ignore[no-untyped-def]
    """REQ_NF_REP_001 — calling ``evaluate`` twice with the same
    state SHALL produce equal proposal tuples (size, expected
    net profit, expected fees all match)."""
    ensemble = _ensemble(
        vols=[Decimal("0.10"), Decimal("0.15")],
        target_vol=Decimal("0.10"),
        portfolio_vol=Decimal("0.12"),
    )
    a = ensemble.evaluate(None)  # type: ignore[arg-type]
    b = ensemble.evaluate(None)  # type: ignore[arg-type]
    assert len(a) == len(b)
    for pa, pb in zip(a, b, strict=True):
        assert pa.size_pct_of_capital == pb.size_pct_of_capital
        assert pa.expected_net_profit == pb.expected_net_profit
        assert pa.expected_fees == pb.expected_fees


# Silence unused-import warning kept for navigational anchor.
_ = pytest
