"""Property-based tests for phase-engine hysteresis — REQ_TP_STR_002.

Properties verified:

- Idempotence: ``resolve_with_hysteresis(amount, ..., current=P)``
  called twice with the same inputs SHALL return the same phase
  on the second call (pure function).
- Upgrade-on-cross: when ``amount`` is well above the current
  phase's upper bound (× 1.05), the engine upgrades.
- Hysteresis floor: when ``amount`` is between the current phase's
  lower bound × (1 - hysteresis) and the lower bound, the engine
  stays on the current phase.
- Downgrade below floor: when ``amount`` is strictly below the
  hysteresis floor, the engine downgrades.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from trading_system.models.phase import Phase
from trading_system.phase_engine.engine import resolve_with_hysteresis


_BOUNDS = [
    Decimal("3000"),
    Decimal("10000"),
    Decimal("50000"),
    Decimal("200000"),
    Decimal("1000000"),
]
_HYSTERESES = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("0.50"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_AMOUNTS = st.decimals(
    min_value=Decimal("100"),
    max_value=Decimal("5_000_000"),
    places=0,
    allow_nan=False,
    allow_infinity=False,
)
_PHASES = st.sampled_from(list(Phase))


# ---------------------------------------------------------------------------
# Idempotence — pure function
# ---------------------------------------------------------------------------


@given(amount=_AMOUNTS, hyst=_HYSTERESES, current=_PHASES)
@settings(max_examples=200)
def test_idempotent_on_repeat(
    amount: Decimal, hyst: Decimal, current: Phase
) -> None:
    """``resolve_with_hysteresis`` is a pure function — calling
    it twice with identical inputs SHALL return the same phase."""
    a = resolve_with_hysteresis(
        amount=amount, bounds=_BOUNDS, hysteresis=hyst, current=current
    )
    b = resolve_with_hysteresis(
        amount=amount, bounds=_BOUNDS, hysteresis=hyst, current=current
    )
    assert a is b


# ---------------------------------------------------------------------------
# Fixed-point: result is stable when fed back as ``current``
# ---------------------------------------------------------------------------


@given(amount=_AMOUNTS, hyst=_HYSTERESES, current=_PHASES)
@settings(max_examples=200)
def test_resolve_is_a_fixed_point(
    amount: Decimal, hyst: Decimal, current: Phase
) -> None:
    """Feeding the engine's output back as ``current`` SHALL
    produce the same output — the resolved phase is a fixed
    point under the same ``amount``."""
    once = resolve_with_hysteresis(
        amount=amount, bounds=_BOUNDS, hysteresis=hyst, current=current
    )
    twice = resolve_with_hysteresis(
        amount=amount, bounds=_BOUNDS, hysteresis=hyst, current=once
    )
    assert once is twice


# ---------------------------------------------------------------------------
# Upgrades fire when amount crosses well above current's upper bound
# ---------------------------------------------------------------------------


@given(hyst=_HYSTERESES)
@settings(max_examples=50)
def test_upgrade_fires_well_above_threshold(hyst: Decimal) -> None:
    """When ``amount`` is ≥ 1.5 × next-threshold and ``current``
    is the lower adjacent phase, the engine SHALL upgrade. The
    multiplier is intentionally generous so any plausible
    hysteresis value can't block the upgrade."""
    # Pick a representative boundary — Phase 2↔3 at 10 000 EUR.
    threshold = _BOUNDS[1]
    huge_amount = threshold * Decimal("1.5")
    result = resolve_with_hysteresis(
        amount=huge_amount,
        bounds=_BOUNDS,
        hysteresis=hyst,
        current=Phase.TWO,
    )
    # Phase moves up to at least THREE (could be higher if the
    # amount also crosses the next threshold).
    assert result.value >= Phase.THREE.value


# ---------------------------------------------------------------------------
# Hysteresis-bound downgrade
# ---------------------------------------------------------------------------


@given(
    hyst=st.decimals(
        min_value=Decimal("0.05"),
        max_value=Decimal("0.20"),
        places=2,
    )
)
@settings(max_examples=50)
def test_downgrade_blocked_inside_hysteresis_band(hyst: Decimal) -> None:
    """Sitting at Phase.TWO with amount in (lower_bound × (1 - hyst),
    lower_bound], the engine SHALL stay at TWO — the hysteresis
    band protects against flapping."""
    threshold = _BOUNDS[0]  # 3 000 — the Phase 1↔2 boundary
    # Inside the band: e.g., hysteresis=0.10 → band is [2700, 3000].
    # Pick 0.5 × band-width above the floor.
    floor = threshold * (Decimal(1) - hyst)
    inside = (threshold + floor) / Decimal(2)  # midpoint of the band
    result = resolve_with_hysteresis(
        amount=inside, bounds=_BOUNDS, hysteresis=hyst, current=Phase.TWO
    )
    assert result is Phase.TWO


@given(
    hyst=st.decimals(
        min_value=Decimal("0.05"),
        max_value=Decimal("0.20"),
        places=2,
    )
)
@settings(max_examples=50)
def test_downgrade_fires_below_hysteresis_floor(hyst: Decimal) -> None:
    """When ``amount`` falls strictly below the hysteresis floor,
    the engine SHALL downgrade."""
    threshold = _BOUNDS[0]
    # 1 EUR below the floor — definitively outside the band.
    below = threshold * (Decimal(1) - hyst) - Decimal(1)
    assume(below > 0)  # avoid degenerate near-zero
    result = resolve_with_hysteresis(
        amount=below, bounds=_BOUNDS, hysteresis=hyst, current=Phase.TWO
    )
    assert result is Phase.ONE


# ---------------------------------------------------------------------------
# Monotonicity — increasing amount never moves the phase down
# ---------------------------------------------------------------------------


@given(
    a=_AMOUNTS,
    b=_AMOUNTS,
    hyst=_HYSTERESES,
    current=_PHASES,
)
@settings(max_examples=200)
def test_monotone_in_amount_at_fixed_current(
    a: Decimal, b: Decimal, hyst: Decimal, current: Phase
) -> None:
    """For the same ``current``, increasing ``amount`` never
    produces a LOWER phase. (The opposite direction may downgrade
    via hysteresis, but going UP can never drop the phase.)"""
    low, high = sorted([a, b])
    res_low = resolve_with_hysteresis(
        amount=low, bounds=_BOUNDS, hysteresis=hyst, current=current
    )
    res_high = resolve_with_hysteresis(
        amount=high, bounds=_BOUNDS, hysteresis=hyst, current=current
    )
    assert res_high.value >= res_low.value
