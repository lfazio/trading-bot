"""Per-aggregate repositories — one read/write surface per concern.

REQ refs: REQ_F_PER_002, REQ_F_PER_006, REQ_F_PER_007, REQ_F_PER_008,
REQ_SDS_PER_002, REQ_SDD_PER_005, REQ_SDD_PER_006, REQ_SDD_PER_007,
REQ_SDD_QNT_007 (HypothesisRepository CR-002 follow-up),
REQ_F_NOT_004 / REQ_F_NOT_005 (TradeApprovalAuditRepository
CR-001 follow-up),
REQ_F_WEB_010 / REQ_SDS_WEB_004 / REQ_SDD_WEB_004
(SqliteIdempotencyStore CR-004 Phase B follow-up),
REQ_F_WEB_003 / REQ_F_WEB_009 / REQ_SDD_WEB_005 / REQ_SDS_WEB_003
(SqliteBacktestJobRepository CR-004 Phase B follow-up).
"""

from trading_system.persistence.repositories.approvals import (
    TradeApprovalAuditRepository,
)
from trading_system.persistence.repositories.backtest import BacktestResultRepository
from trading_system.persistence.repositories.backtest_jobs import (
    SqliteBacktestJobRepository,
)
from trading_system.persistence.repositories.idempotency import (
    SqliteIdempotencyStore,
)
from trading_system.persistence.repositories.portfolio import PortfolioRepository
from trading_system.persistence.repositories.quant import HypothesisRepository
from trading_system.persistence.repositories.registry import RegistryRepository
from trading_system.persistence.repositories.snapshot import (
    KillSwitchSnapshotRepository,
)
from trading_system.persistence.repositories.transition import TransitionRepository

__all__ = [
    "BacktestResultRepository",
    "HypothesisRepository",
    "KillSwitchSnapshotRepository",
    "PortfolioRepository",
    "RegistryRepository",
    "SqliteBacktestJobRepository",
    "SqliteIdempotencyStore",
    "TradeApprovalAuditRepository",
    "TransitionRepository",
]
