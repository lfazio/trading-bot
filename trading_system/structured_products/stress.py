"""Stress-scenario evaluation for ``Decomposition`` (REQ_F_STP_005,
REQ_SDD_ALG_013).

Three scenarios are applied; if any one's PnL is worse than the
product's stated worst-case loss, the candidate is rejected:

- crash: -20% drop on the equity equivalent, amplified by
  ``1 + hidden_leverage`` to capture leveraged downside.
- vol expansion: vol x3 → -30% on the equity equivalent.
- correlation spike: cross-asset correlations move toward 1 → -15%
  on the equity equivalent.

The function returns a boolean; the admission gate emits the
categorised Err.
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.structured_products.decomposition import Decomposition

_CRASH_SHOCK = Decimal("0.20")
_VOL_SHOCK = Decimal("0.30")
_CORR_SHOCK = Decimal("0.15")


def stress_pass(decomp: Decomposition) -> bool:
    """``True`` iff every scenario's loss is bounded by the
    decomposition's stated worst-case loss."""
    if decomp.equity_equiv == 0:
        # Cash-equivalent products have no scenario exposure;
        # vacuously pass.
        return True
    crash_loss = decomp.equity_equiv * _CRASH_SHOCK * (Decimal(1) + decomp.hidden_leverage)
    vol_loss = decomp.equity_equiv * _VOL_SHOCK
    corr_loss = decomp.equity_equiv * _CORR_SHOCK
    worst = decomp.worst_case_loss
    # Each loss is non-negative (magnitude); compare directly.
    return crash_loss <= worst and vol_loss <= worst and corr_loss <= worst
