"""``PhaseEngine`` — phase state machine with hysteresis on downgrade.

Mostly pure: the bulk of the logic lives in two top-level functions
(``natural_phase_for_amount`` and ``resolve_with_hysteresis``) that are
testable in isolation. The ``PhaseEngine`` class threads the previous
phase through those functions and exposes the SDS §5.2 distribution
surface (``constraints_for(phase)``) — REQ_SDS_FLO_002.

The class holds *instance-level* mutable state (the current phase),
which is consistent with REQ_SDD_IMP_006 (no module-level mutable
state) — REQ_SDS_ARC_002's "engines as pure functions" guidance is
honored by the pure resolver helpers; the class wraps them.

Internal-error handling: malformed ``bounds`` / ``hysteresis`` /
``constraints`` mappings are programmer errors detected at
construction; they raise ``ValueError`` per REQ_SDD_ERR_001.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading_system.models.money import Money
from trading_system.models.phase import Phase, PhaseConstraints

_NUM_PHASES = 6
_NUM_BOUNDS = _NUM_PHASES - 1


def natural_phase_for_amount(amount: Decimal, bounds: list[Decimal]) -> Phase:
    """Return the phase whose half-open band [b_{n-1}, b_n) contains
    ``amount``, ignoring hysteresis. ``bounds`` MUST have exactly 5
    entries in strictly ascending order; this is a precondition (the
    engine validates it at construction).
    """
    for i, b in enumerate(bounds, start=1):
        if amount < b:
            return Phase(i)
    return Phase.SIX


def resolve_with_hysteresis(
    *,
    amount: Decimal,
    bounds: list[Decimal],
    hysteresis: Decimal,
    current: Phase,
) -> Phase:
    """Pure phase resolver with hysteresis on downgrade
    (REQ_F_CAP_005, REQ_SDD_ALG_002).

    - Upgrade is *immediate*: if the natural phase exceeds ``current``,
      the engine moves to it as soon as ``amount`` clears the boundary.
    - Downgrade requires ``amount`` to fall **below** the lower-phase
      upper bound by ``hysteresis`` * that bound. For example, the
      Phase 2 -> Phase 1 boundary is 3 000 EUR; with hysteresis 0.10,
      a downgrade only fires below 2 700 EUR.
    - At ``Phase.ONE`` there is nothing to downgrade to, so hysteresis
      doesn't apply.

    The function is total (no Result wrapping) because all inputs
    have been validated at the engine boundary.
    """
    target = natural_phase_for_amount(amount, bounds)
    if target == current:
        return current
    if target > current:
        return target
    if current == Phase.ONE:
        return current  # already at floor
    boundary = bounds[current.value - 2]
    threshold = boundary * (Decimal(1) - hysteresis)
    if amount < threshold:
        return target
    return current


@dataclass(slots=True)
class PhaseEngine:
    """Phase state machine.

    Construct via ``PhaseEngine(bounds=..., hysteresis=...,
    constraints=..., initial_phase=...)`` or via the
    ``load_phase_engine`` YAML loader. The ``initial_phase`` defaults
    to ``Phase.ONE``; production code should pass the equity-derived
    natural phase at boot.
    """

    bounds: list[Decimal]
    hysteresis: Decimal
    constraints: dict[Phase, PhaseConstraints]
    initial_phase: Phase = Phase.ONE
    _current: Phase = field(init=False)

    def __post_init__(self) -> None:
        if len(self.bounds) != _NUM_BOUNDS:
            raise ValueError(
                f"PhaseEngine.bounds must have exactly {_NUM_BOUNDS} entries "
                f"(got {len(self.bounds)})"
            )
        for i in range(1, len(self.bounds)):
            if self.bounds[i] <= self.bounds[i - 1]:
                raise ValueError(
                    f"PhaseEngine.bounds must be strictly ascending; got {self.bounds}"
                )
        if not (Decimal(0) <= self.hysteresis < Decimal(1)):
            raise ValueError(f"PhaseEngine.hysteresis must lie in [0, 1), got {self.hysteresis}")
        for phase in Phase:
            if phase not in self.constraints:
                raise ValueError(f"PhaseEngine.constraints missing entry for {phase!r}")
        if self.constraints[Phase.FIVE].portfolio_vol_cap is None:
            raise ValueError(
                "PhaseEngine.constraints[Phase.FIVE].portfolio_vol_cap must be set "
                "(REQ_F_CAP_012 — Phase 5 portfolio-level vol cap mandatory)"
            )
        if self.constraints[Phase.SIX].portfolio_vol_cap is None:
            raise ValueError(
                "PhaseEngine.constraints[Phase.SIX].portfolio_vol_cap must be set "
                "(REQ_F_CAP_012 — Phase 6 portfolio-level vol cap mandatory)"
            )
        self._current = self.initial_phase

    # ------------------------------------------------------------------
    # Public surface (REQ_SDS_FLO_002)
    # ------------------------------------------------------------------

    def current(self) -> Phase:
        """Return the phase the engine is currently sitting at."""
        return self._current

    def resolve(self, total_capital: Money) -> Phase:
        """Update and return the active phase given ``total_capital``
        (``equity + injected_capital``). Caller-side currency is
        out of scope here — the engine treats ``total_capital.amount``
        as a reporting-currency-denominated value.
        """
        self._current = resolve_with_hysteresis(
            amount=total_capital.amount,
            bounds=self.bounds,
            hysteresis=self.hysteresis,
            current=self._current,
        )
        return self._current

    def constraints_for(self, phase: Phase) -> PhaseConstraints:
        """Return the ``PhaseConstraints`` for ``phase``. Distinct
        from ``current()`` so callers can inspect any phase's
        constraints (e.g., for milestone-controller previews)."""
        return self.constraints[phase]
