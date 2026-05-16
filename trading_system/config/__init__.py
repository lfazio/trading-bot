"""Centralised configuration validation.

The system already ships per-module loaders (each emits categorised
``Err`` strings). This package adds a single ``validate_all`` entry
point that drives every shipped loader against the corresponding
YAML in one pass, so a typo in any file fails the startup gate
rather than waiting for the YAML to be loaded on-demand.

Satisfies REQ_SDS_CFG_001 ("Configuration SHALL be validated at
startup; invalid configuration SHALL be a fatal error") and
REQ_SDD_ERR_002 (categorised Errs).
"""

from __future__ import annotations

from trading_system.config.system import (
    SystemConfig,
    load_system_config,
)
from trading_system.config.validator import (
    ValidationReport,
    validate_all,
)

__all__ = [
    "SystemConfig",
    "ValidationReport",
    "load_system_config",
    "validate_all",
]
