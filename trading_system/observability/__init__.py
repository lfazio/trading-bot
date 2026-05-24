"""Structured logging primitives.

Satisfies REQ_NF_LOG_001 (timestamped logs for trades / KS events /
meta-loop reports) and REQ_SDS_CRS_001 (JSON-line schema:
``{"ts", "category", "corr_id", "payload"}``).

The module ships infrastructure only — existing call sites continue
to use ``logging.getLogger(__name__)`` directly. New code (CR-006
tick fan-out, CR-001 notification fan-out, …) emits via
``structured_log`` so the per-tick / per-account correlation lands
in the JSON envelope without each caller touching the formatter.
"""

from __future__ import annotations

from trading_system.observability.logger import (
    HUMAN_FORMAT,
    JsonLineFormatter,
    LogCategory,
    LogContext,
    configure_logging,
    current_context,
    log_scope,
    structured_log,
)
from trading_system.observability.loader import (
    LoggingConfig,
    load_logging_config,
)

__all__ = [
    "HUMAN_FORMAT",
    "JsonLineFormatter",
    "LogCategory",
    "LogContext",
    "LoggingConfig",
    "configure_logging",
    "current_context",
    "load_logging_config",
    "log_scope",
    "structured_log",
]
