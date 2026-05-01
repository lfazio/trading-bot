"""Domain models — pure data types with input validation only.

No I/O, no business logic, no module-level mutable state
(REQ_SDS_MOD_002, REQ_SDD_IMP_006). Every type is an immutable
``@dataclass(frozen=True, slots=True)`` value; constructors validate
invariants and raise ``ValueError`` on bad inputs (REQ_SDD_ERR_001 —
type-construction is the one sanctioned ``raise`` site).

Module layout:
- ``money``         — ``Currency``, ``Money`` (REQ_SDD_TYP_001, REQ_SDD_TYP_003)
- ``identifiers``   — typed ID aliases (REQ_SDD_TYP_002)
- ``instrument``    — ``Instrument`` / ``Stock`` / ``Turbo`` /
                       ``StructuredProduct`` / ``InstrumentClass``
                       (REQ_F_BRK_001, REQ_F_TRB_006, REQ_F_STP_002)
- ``trading``       — ``Side`` / ``OrderType`` / ``OrderStatus`` /
                       ``StopLoss`` / ``Order`` / ``Trade`` /
                       ``Position`` / ``Dividend``
                       (REQ_SDD_DAT_001 / 002 / 005 / 006,
                        REQ_F_CAP_014)
- ``phase``         — ``Phase`` (IntEnum 1..6) / ``PhaseConstraints`` /
                       ``MarketRegime`` (REQ_F_CAP_003 / 006-013,
                       REQ_SDD_DAT_007, REQ_SDD_TYP_003)
- ``flow``          — ``Injection`` / ``EquityPoint``
                       (REQ_F_CFL_001, REQ_SDD_DAT_003)
- ``safety``        — ``KillSwitchState`` / ``TriggerCategory`` /
                       ``KillSwitchTrigger`` (REQ_S_KS_001,
                       REQ_SDD_DAT_008)
- ``meta``          — ``ImprovementReport`` / ``TradeProposal`` /
                       ``ValidationResult`` (REQ_F_MTO_007)

Cross-currency operations on ``Money`` raise ``AssertionError`` (panic)
because they indicate a programmer error, not a recoverable failure.
"""

from trading_system.models.flow import EquityPoint, Injection
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    SnapshotId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import (
    Instrument,
    InstrumentClass,
    Stock,
    StructuredProduct,
    Turbo,
)
from trading_system.models.meta import (
    ImprovementReport,
    TradeProposal,
    ValidationResult,
)
from trading_system.models.money import Currency, Money
from trading_system.models.phase import MarketRegime, Phase, PhaseConstraints
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.models.trading import (
    Dividend,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    StopLoss,
    Trade,
)

__all__ = [
    "Currency",
    "Dividend",
    "EquityPoint",
    "ImprovementReport",
    "Injection",
    "Instrument",
    "InstrumentClass",
    "InstrumentId",
    "KillSwitchState",
    "KillSwitchTrigger",
    "MarketRegime",
    "Money",
    "Order",
    "OrderId",
    "OrderStatus",
    "OrderType",
    "Phase",
    "PhaseConstraints",
    "Position",
    "Side",
    "SnapshotId",
    "Stock",
    "StopLoss",
    "StrategyId",
    "StructuredProduct",
    "Trade",
    "TradeId",
    "TradeProposal",
    "TriggerCategory",
    "Turbo",
    "ValidationResult",
]
