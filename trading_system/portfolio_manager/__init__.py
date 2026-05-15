"""Portfolio management — CR-005 Phase-5 implementation (algorithmic core).

Sits at L4 between ``strategies/`` and ``risk/`` in the runtime
topology. Generates structured proposals (rebalance, sector rotation,
tax harvest) and extends the existing attribution surface with
multi-scope decomposition. The risk engine continues to gate every
emitted proposal — no Protocol or invariant change.

v1 ships the **algorithmic core**: every generator is a pure
function callable end-to-end against an immutable Portfolio view +
the relevant context. The actual ``strategies/`` → ``portfolio_manager/``
→ ``risk/`` runtime wiring is a Phase-6 follow-up alongside CR-006
(REQ_F_PMG_008).

Public surface:

- ``Rebalancer`` — drift-from-target proposals (REQ_F_PMG_002).
- ``SectorRotatorFacade`` — wraps CR-010 ``RotationProposal`` rows
  into ``TradeProposal`` rows (REQ_F_PMG_003).
- ``TaxHarvesterFacade`` — wraps ``tax/harvest.py.HarvestSuggestion``
  rows into SELL ``TradeProposal`` rows (REQ_F_PMG_004); silently
  drops stale suggestions for non-held positions.
- ``AttributionDecomposition`` — multi-scope NAV decomposition with
  a ``sum-to-NAV ± 1e-9`` invariant (REQ_F_PMG_005 / REQ_SDD_PMG_004).
- ``RebalanceProposal`` — frozen proposal row.
- ``Cadence`` — scheduler-frequency Literal (REQ_F_PMG_006).

REQ refs: REQ_F_PMG_001..008, REQ_SDS_PMG_001..002,
REQ_SDD_PMG_001..004.
"""

from trading_system.portfolio_manager.attribution import (
    AttributionDecomposition,
    attribution_decomposition,
)
from trading_system.portfolio_manager.proposal import (
    Cadence,
    RebalanceDirection,
    RebalanceProposal,
)
from trading_system.portfolio_manager.rebalancer import Rebalancer
from trading_system.portfolio_manager.sector_rotator_facade import (
    SectorRotatorFacade,
)
from trading_system.portfolio_manager.tax_harvester_facade import (
    HarvestablePosition,
    TaxHarvesterFacade,
)

__all__ = [
    "AttributionDecomposition",
    "Cadence",
    "HarvestablePosition",
    "RebalanceDirection",
    "RebalanceProposal",
    "Rebalancer",
    "SectorRotatorFacade",
    "TaxHarvesterFacade",
    "attribution_decomposition",
]
