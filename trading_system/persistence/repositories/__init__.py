"""Per-aggregate repositories — one read/write surface per concern.

REQ refs: REQ_F_PER_002, REQ_F_PER_006, REQ_F_PER_007, REQ_F_PER_008,
REQ_SDS_PER_002, REQ_SDD_PER_005, REQ_SDD_PER_006, REQ_SDD_PER_007.
"""

from trading_system.persistence.repositories.backtest import BacktestResultRepository
from trading_system.persistence.repositories.portfolio import PortfolioRepository
from trading_system.persistence.repositories.registry import RegistryRepository
from trading_system.persistence.repositories.snapshot import (
    KillSwitchSnapshotRepository,
)

__all__ = [
    "BacktestResultRepository",
    "KillSwitchSnapshotRepository",
    "PortfolioRepository",
    "RegistryRepository",
]
