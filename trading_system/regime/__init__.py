"""Market-regime detection — CR-013 Phase-5 implementation.

The runtime's canonical source of ``MarketRegime``. Consumers (sector
rotator, structured-products admission, risk-engine regime gate) read
the regime from here rather than computing their own classification
(REQ_SDS_RGM_001).

Public surface:

- ``RegimeDetector.evaluate(bars)`` — pure function over bars + frozen
  ``RegimeConfig`` returning a ``MarketRegime`` (REQ_F_RGM_001 /
  REQ_F_RGM_002).
- ``RULE_ORDER`` — the documented tie-break order constant
  (REQ_F_RGM_003 / REQ_SDD_RGM_001).
- ``TransitionTracker`` — single mutable cursor; emits
  ``TransitionEvent`` only after ``confirmation_periods`` consecutive
  same-regime observations (REQ_F_RGM_004 / REQ_SDD_RGM_003).
- ``RegimeConfig`` — frozen parameters loaded from ``config/regime.yaml``
  with documented defaults (REQ_F_RGM_006).
- ``BarSource`` Protocol — pluggable bar provider for the detector
  (REQ_F_RGM_006 / REQ_SDS_RGM_002).

REQ refs: REQ_F_RGM_001..006, REQ_NF_RGM_001, REQ_SDS_RGM_001..002,
REQ_SDD_RGM_001..005.
"""

from trading_system.regime.bar_source import BarSource
from trading_system.regime.config import RegimeConfig
from trading_system.regime.detector import RULE_ORDER, RegimeDetector
from trading_system.regime.transition import TransitionEvent, TransitionTracker

# ``RegimeOrchestrator`` lives in ``regime.orchestrator`` and depends on
# ``persistence.repositories.transition`` — importing it eagerly here
# would cycle through ``persistence.mappers`` → ``regime.transition``
# (which is still inside this package's __init__). Callers that need
# the orchestrator import it directly:
#
#     from trading_system.regime.orchestrator import RegimeOrchestrator
#
# The pure ``regime/`` core (detector + tracker + config) has no
# persistence dependency and stays re-exported here.

__all__ = [
    "RULE_ORDER",
    "BarSource",
    "RegimeConfig",
    "RegimeDetector",
    "TransitionEvent",
    "TransitionTracker",
]
