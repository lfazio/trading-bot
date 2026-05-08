"""Phase-5+ sector rotation engine.

Off below phase 5 by construction (REQ_F_SCT_001 + REQ_SDS_SCT_002):
``SectorRotator.evaluate(state, phase)`` short-circuits to ``()``
whenever ``phase < Phase.FIVE``, regardless of the regime / screener
/ holding-state inputs.

Public surface:
- ``SectorTaxonomy`` — operator-supplied canonical sector vocabulary.
- ``RegimeSectorBias`` — frozen ``MarketRegime -> dict[sector, weight]``.
- ``RotationPolicy`` — frozen knobs (min_holding_days, quarter cap,
  whipsaw dampener).
- ``HoldingState`` — single mutable cursor (REQ_SDS_SCT_003 +
  REQ_SDD_SCT_005).
- ``SectorRotator`` — emits ``RotationProposal`` rows.

REQ refs: REQ_F_SCT_001..007, REQ_NF_SCT_001, REQ_SDS_SCT_001..003,
REQ_SDD_SCT_001..007.
"""

from trading_system.wealth_ops.sector_rotator.policy import (
    HoldingState,
    RotationPolicy,
)
from trading_system.wealth_ops.sector_rotator.regime_sector_bias import (
    RegimeSectorBias,
)
from trading_system.wealth_ops.sector_rotator.taxonomy import SectorTaxonomy

__all__ = [
    "HoldingState",
    "RegimeSectorBias",
    "RotationPolicy",
    "SectorTaxonomy",
]
