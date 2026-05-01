"""Kill-switch state and trigger types.

REQ refs:
- REQ_S_KS_001 — three states (ACTIVE / DEGRADED / KILL).
- REQ_S_KS_007 — every state transition produces an audit snapshot;
  the ``snapshot_id`` field references it.
- REQ_SDD_DAT_008 — ``snapshot_id`` is non-empty.
- REQ_SDD_TYP_003 — enums as ``StrEnum``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from trading_system.models.identifiers import SnapshotId


class KillSwitchState(StrEnum):
    """Three kill-switch states ordered by severity."""

    ACTIVE = "active"
    DEGRADED = "degraded"
    KILL = "kill"


class TriggerCategory(StrEnum):
    """Trigger source bucket — drives downstream operator messaging."""

    FINANCIAL = "financial"
    STRATEGY = "strategy"
    EXECUTION = "execution"
    INTEGRITY = "integrity"


TriggerSeverity = Literal["DEGRADE", "KILL"]


@dataclass(frozen=True, slots=True)
class KillSwitchTrigger:
    """A single kill-switch event. ``snapshot_id`` MUST reference an
    existing audit-log artifact (REQ_SDD_DAT_008, REQ_NF_AUD_001)."""

    category: TriggerCategory
    code: str
    message: str
    severity: TriggerSeverity
    raised_at: datetime
    snapshot_id: SnapshotId

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("KillSwitchTrigger.code must be non-empty")
        if not self.message:
            raise ValueError("KillSwitchTrigger.message must be non-empty")
        if self.severity not in ("DEGRADE", "KILL"):
            raise ValueError(
                f"KillSwitchTrigger.severity must be DEGRADE or KILL, got {self.severity!r}"
            )
        if not self.snapshot_id:
            raise ValueError("KillSwitchTrigger.snapshot_id must be non-empty")
