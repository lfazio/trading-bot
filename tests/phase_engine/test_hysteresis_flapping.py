"""Hysteresis flapping fixture — REQ_SDD_TST_004.

REQ_SDD_TST_004 — Phase-engine tests SHALL include a
hysteresis-flapping fixture that traverses each boundary in both
directions; no boundary SHALL produce more than one transition per
traversal.

The test walks every adjacent-phase boundary (1↔2, 2↔3, 3↔4, 4↔5,
5↔6) with a sequence of equity values designed to provoke spurious
transitions: cross the boundary, walk inside the hysteresis band a
few times, cross back. The engine SHALL emit exactly one transition
per direction — no oscillation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.phase import Phase
from trading_system.phase_engine.engine import resolve_with_hysteresis


_BOUNDS = [
    Decimal("3000"),
    Decimal("10000"),
    Decimal("50000"),
    Decimal("200000"),
    Decimal("1000000"),
]
_HYST = Decimal("0.10")  # 10 %

# Adjacent-phase boundaries: (lower_phase, upper_phase, threshold).
_BOUNDARIES = [
    (Phase.ONE, Phase.TWO, _BOUNDS[0]),
    (Phase.TWO, Phase.THREE, _BOUNDS[1]),
    (Phase.THREE, Phase.FOUR, _BOUNDS[2]),
    (Phase.FOUR, Phase.FIVE, _BOUNDS[3]),
    (Phase.FIVE, Phase.SIX, _BOUNDS[4]),
]


@pytest.mark.parametrize(
    "lower, upper, threshold",
    _BOUNDARIES,
    ids=[f"{lo.name}-{hi.name}" for (lo, hi, _) in _BOUNDARIES],
)
def test_no_flapping_around_boundary(
    lower: Phase, upper: Phase, threshold: Decimal
) -> None:
    """REQ_SDD_TST_004 — walk the boundary in both directions and
    count transitions; expect exactly two (one up, one down)."""
    # Build a flap-prone equity sequence:
    #   * starts well below threshold (clearly lower phase)
    #   * crosses upward (1 transition up)
    #   * oscillates inside the hysteresis band (no transitions)
    #   * drops below the hysteresis floor (1 transition down)
    #   * oscillates inside the band (no transitions)
    above_band = threshold * Decimal("1.05")
    inside_band_high = threshold * Decimal("1.02")
    inside_band_low = threshold * Decimal("0.95")  # above 0.9 (hyst floor)
    below_band = threshold * Decimal("0.85")  # below 0.9 (hyst floor)

    sequence = [
        threshold * Decimal("0.5"),  # well below
        threshold * Decimal("0.6"),
        above_band,                  # cross UP — transition 1
        inside_band_high,
        inside_band_low,
        inside_band_high,
        inside_band_low,
        below_band,                  # cross DOWN — transition 2
        inside_band_low,
        below_band,
    ]

    current = lower
    transitions: list[tuple[Phase, Phase]] = []
    for amount in sequence:
        new = resolve_with_hysteresis(
            amount=amount, bounds=_BOUNDS, hysteresis=_HYST, current=current
        )
        if new is not current:
            transitions.append((current, new))
            current = new

    assert len(transitions) == 2, (
        f"REQ_SDD_TST_004 — boundary {lower.name}↔{upper.name}: "
        f"expected exactly 2 transitions, got {len(transitions)}: {transitions}"
    )
    assert transitions[0] == (lower, upper), (
        f"first transition SHALL be the upgrade {lower.name}->{upper.name}, "
        f"got {transitions[0]}"
    )
    assert transitions[1] == (upper, lower), (
        f"second transition SHALL be the downgrade {upper.name}->{lower.name}, "
        f"got {transitions[1]}"
    )


def test_no_double_transitions_under_rapid_oscillation() -> None:
    """REQ_SDD_TST_004 closed-set — even pathological rapid
    oscillation near a boundary SHALL produce exactly one
    transition per direction. Walks the 10 000 boundary (Phase 2
    ↔ Phase 3) with 20 alternating values inside the hysteresis
    band; assert state never leaves the latched phase."""
    threshold = _BOUNDS[1]  # 10 000
    inside_high = threshold * Decimal("1.05")
    inside_low = threshold * Decimal("0.95")  # inside hyst band

    current = Phase.THREE  # latched at upper
    transitions = 0
    for _ in range(20):
        # Bounce between inside-high and inside-low — both within hyst band.
        for amount in (inside_high, inside_low):
            new = resolve_with_hysteresis(
                amount=amount,
                bounds=_BOUNDS,
                hysteresis=_HYST,
                current=current,
            )
            if new is not current:
                transitions += 1
                current = new

    assert transitions == 0, (
        f"REQ_SDD_TST_004 — rapid oscillation inside the hysteresis "
        f"band SHALL NOT cause transitions; saw {transitions}"
    )
