"""Phase-aware constraint resolver for the onboarding wizard.

Reads ``config/phases.yaml``, derives the natural phase from the
operator's starting capital via the documented bounds, and
returns the matching ``PhaseConstraints``.

Lives under ``webapp/runtimes/`` because the structural audit
forbids the view tier from importing ``trading_system.phase_engine``
+ ``trading_system.config``.

Falls back to a Phase-1 constraint set when the loader fails so
a broken config doesn't break onboarding.
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.models.phase import AllocationBucket, PhaseConstraints


def _phase_one_fallback() -> PhaseConstraints:
    return PhaseConstraints(
        max_positions=3,
        max_trades_per_month=4,
        allocation_targets={
            AllocationBucket.STOCK: Decimal("0.90"),
            AllocationBucket.TACTICAL: Decimal("0.10"),
        },
        turbo_exposure_max=Decimal("0"),
        risk_per_trade_band=(Decimal("0.01"), Decimal("0.02")),
        max_drawdown=Decimal("0.15"),
    )


def phase_constraints_for_capital(
    capital: Decimal,
    *,
    config_dir: str = "config",
) -> PhaseConstraints:
    """Pick the constraints matching the natural phase for the
    given starting capital. Defaults to the Phase-1 fallback when
    the config loader fails or returns an unexpected shape.
    """
    try:
        from pathlib import Path

        from trading_system.phase_engine.engine import (
            natural_phase_for_amount,
        )
        from trading_system.phase_engine.loader import load_phase_engine

        result = load_phase_engine(Path(config_dir) / "phases.yaml")
    except Exception:  # noqa: BLE001 — defensive
        return _phase_one_fallback()
    if not hasattr(result, "is_ok") or not result.is_ok():
        return _phase_one_fallback()
    engine = result.unwrap()
    phase = natural_phase_for_amount(capital, engine.bounds)
    constraints = engine.constraints.get(phase)
    if constraints is None:
        return _phase_one_fallback()
    return constraints
