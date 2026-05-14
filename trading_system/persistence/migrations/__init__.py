"""Schema migrations (forward-only, SHA-locked).

REQ refs: REQ_F_PER_004, REQ_SDS_PER_003, REQ_SDD_PER_004.
"""

from trading_system.persistence.migrations.runner import MigrationRunner

__all__ = ["MigrationRunner"]
