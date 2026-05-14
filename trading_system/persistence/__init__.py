"""SQLite-backed system-of-record (CR-008).

The persistence layer is the durable home for every aggregate the
engine produces — equity curves, positions, strategy registry, kill-
switch snapshots, backtest results, CR-013/014/015's stateful
sub-systems.

Public surface:

- ``Connection`` — opens SQLite with WAL + the pinned PRAGMA set;
  the **only** module in the codebase that calls
  ``sqlite3.connect`` (REQ_F_PER_010 / REQ_SDD_PER_001).
- ``MigrationRunner`` — idempotent, SHA-locked, supports
  ``--dry-run`` (REQ_F_PER_004 / REQ_SDD_PER_004).
- Repositories under ``persistence.repositories`` — one per
  aggregate root, all returning ``Result[T, str]`` at their public
  surface (REQ_F_PER_002 / REQ_SDS_PER_002).

REQ refs: REQ_F_PER_001..010, REQ_NF_PER_001, REQ_SDS_PER_001..004,
REQ_SDD_PER_001..008.
"""

from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.backtest import BacktestResultRepository
from trading_system.persistence.repositories.portfolio import PortfolioRepository
from trading_system.persistence.repositories.registry import RegistryRepository
from trading_system.persistence.repositories.snapshot import (
    KillSwitchSnapshotRepository,
)

__all__ = [
    "BacktestResultRepository",
    "Connection",
    "KillSwitchSnapshotRepository",
    "MigrationRunner",
    "PortfolioRepository",
    "RegistryRepository",
]
