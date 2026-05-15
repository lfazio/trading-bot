"""Currency hedger — CR-011 Phase-5 implementation.

Sits at L3 inside ``wealth_ops/`` alongside ``sector_rotator/``
(CR-010). Closes the Phase-5 wealth-preservation hedging gap from
CLAUDE.md ("currency hedging on non-EUR exposure"). v1 ships the
pure-function exposure + proposals + an append-only ledger of
``FXForward`` rows; the risk-engine + dashboard wiring is a Phase-6
follow-up.

Public surface:

- ``compute_fx_exposure`` — pure function returning per-currency
  exposure share against the base currency (REQ_F_FXH_002).
- ``FXHedger`` — proposes hedges from exposures + policy
  (REQ_F_FXH_003 — strict-above-threshold; notional = exposure ×
  target_hedge_ratio; deterministic Currency.value sort order).
- ``HedgePolicy`` — frozen dataclass with documented defaults
  (REQ_F_FXH_004).
- ``HedgeProposal`` / ``FXForward`` / ``FXForwardState`` — frozen
  row shapes for the audit ledger (REQ_F_FXH_005).
- ``FXHedgeLedger`` — append-only ledger; single mutable element of
  the package (REQ_F_FXH_005 / REQ_F_FXH_006).
- ``mark`` — pure mark-to-market formula (REQ_F_FXH_005 /
  REQ_SDD_FXH_005).

REQ refs: REQ_F_FXH_001..006, REQ_NF_FXH_001, REQ_SDS_FXH_001..002,
REQ_SDD_FXH_001..005.
"""

from trading_system.wealth_ops.fx_hedger.exposure import (
    MarkedPosition,
    compute_fx_exposure,
)
from trading_system.wealth_ops.fx_hedger.forward import (
    FXForward,
    FXForwardState,
    ForwardId,
    HedgeProposal,
)
from trading_system.wealth_ops.fx_hedger.hedger import FXHedger
from trading_system.wealth_ops.fx_hedger.ledger import FXHedgeLedger, mark
from trading_system.wealth_ops.fx_hedger.policy import HedgePolicy

__all__ = [
    "FXForward",
    "FXForwardState",
    "FXHedgeLedger",
    "FXHedger",
    "ForwardId",
    "HedgePolicy",
    "HedgeProposal",
    "MarkedPosition",
    "compute_fx_exposure",
    "mark",
]
