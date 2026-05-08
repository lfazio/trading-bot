"""Performance and monitoring computations.

REQ refs: REQ_F_PRT_002 (NAV-style reporting + attribution),
REQ_NF_LOG_001 (timestamped trades / KS events / improvement
reports — logging hook surface), REQ_SDS_MOD_015-adjacent
(``dashboard/`` consumes this module read-only).
"""

from trading_system.analytics.engine import Analytics, PerformanceSummary

__all__ = ["Analytics", "PerformanceSummary"]
