"""Safety layer (kill switch).

Concrete implementation per SDD §3.13 / §4.8 / §5.5:

- ``protocol.SafetyLayer`` — the surface engines depend on.
- ``state_manager.StateManager`` — single writer for
  ``KillSwitchState`` (REQ_SDS_MOD_010); applies triggers and
  produces audit snapshots on every transition.
- ``snapshot`` — ``AuditSnapshot``, ``MemorySnapshotSink``,
  ``FileSnapshotSink`` (REQ_NF_AUD_001 / REQ_SDS_CRS_002).
- ``alerts`` — ``AlertChannel`` Protocol + ``MemoryAlertChannel``
  test double + ``deliver_with_retry`` exponential backoff
  (REQ_SDS_INT_003 / REQ_SDD_ERR_005).
- ``recovery`` — ``RecoveryConditions`` + token verifiers
  (REQ_S_KS_009).
- ``anomaly`` — pure-function detectors (single-day loss, rapid
  decline) the monitor calls (REQ_SDD_ALG_006 / REQ_SDD_ALG_007).
- ``loader`` — YAML loader for ``config/kill_switch.yaml``.

REQ refs:
- REQ_S_KS_001..012 — kill switch states, triggers, recovery.
- REQ_SDS_MOD_010 — single writer for state.
- REQ_SDD_API_002 / REQ_SDD_API_003 — runtime-checkable Protocol;
  ``must_halt()`` is O(1) with no I/O.
"""

from trading_system.safety.alerts import (
    AlertChannel,
    FlakyAlertChannel,
    MemoryAlertChannel,
    deliver_with_retry,
)
from trading_system.safety.anomaly import (
    rapid_decline_breach,
    single_day_loss_breach,
)
from trading_system.safety.loader import (
    ExecutionTriggerConfig,
    FinancialTriggerConfig,
    KillSwitchTriggerConfig,
    load_kill_switch_config,
)
from trading_system.safety.protocol import SafetyLayer
from trading_system.safety.recovery import (
    AlwaysInvalidVerifier,
    AlwaysValidVerifier,
    HmacOperatorTokenVerifier,
    OperatorTokenVerifier,
    RecoveryConditions,
)
from trading_system.safety.snapshot import (
    AuditSnapshot,
    FileSnapshotSink,
    MemorySnapshotSink,
    SnapshotSink,
)
from trading_system.safety.state_manager import StateManager, StateManagerConfig

__all__ = [
    "AlertChannel",
    "AlwaysInvalidVerifier",
    "AlwaysValidVerifier",
    "AuditSnapshot",
    "ExecutionTriggerConfig",
    "FileSnapshotSink",
    "FinancialTriggerConfig",
    "FlakyAlertChannel",
    "HmacOperatorTokenVerifier",
    "KillSwitchTriggerConfig",
    "MemoryAlertChannel",
    "MemorySnapshotSink",
    "OperatorTokenVerifier",
    "RecoveryConditions",
    "SafetyLayer",
    "SnapshotSink",
    "StateManager",
    "StateManagerConfig",
    "deliver_with_retry",
    "load_kill_switch_config",
    "rapid_decline_breach",
    "single_day_loss_breach",
]
