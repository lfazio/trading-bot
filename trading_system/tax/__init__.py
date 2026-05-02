"""Tax engine — France CTO / PFU pure-function implementation.

Module-level state: none. Side effects: none. All functions take a
``TaxConfig`` explicitly so the engine is configuration-driven and
testable in isolation (REQ_SDS_MOD_003, REQ_SDD_IMP_006).

REQ refs:
- REQ_F_TAX_001 — net gain = gross x (1 - rate); default rate 0.30.
- REQ_F_TAX_002 — net dividend identical formula.
- REQ_F_TAX_003 — trade gate: expected_net_profit > gate_multiplier x fees
  AFTER tax. Default multiplier 5.
- REQ_F_TAX_004 — engine never exposes pre-tax optimization signals.
- REQ_F_TAX_006 — Phase-5+ tax-loss harvester finds in-year offsets.
- REQ_C_TAX_001 — France CTO (PFU) is the only supported regime.
- REQ_SDD_ALG_001 — round HALF_UP to 2 decimal places.
- REQ_SDD_CFG_001 / REQ_SDD_CFG_002 — defaults are 0.30 and 5.
"""

from trading_system.tax.config import TaxConfig
from trading_system.tax.engine import net_dividend, net_gain, trade_passes_gate
from trading_system.tax.harvest import HarvestSuggestion, Realization, harvest_losses

__all__ = [
    "HarvestSuggestion",
    "Realization",
    "TaxConfig",
    "harvest_losses",
    "net_dividend",
    "net_gain",
    "trade_passes_gate",
]
