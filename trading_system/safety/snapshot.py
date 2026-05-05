"""Audit snapshot — full state captured on every KS state transition.

REQ refs: REQ_NF_AUD_001 (full state snapshot per KS event),
REQ_SDS_CRS_002 (snapshot persisted to a tamper-evident audit log),
REQ_S_KS_007 (kill-switch action sequence includes snapshot write).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import KillSwitchState


@dataclass(frozen=True, slots=True)
class AuditSnapshot:
    """Frozen snapshot persisted on every kill-switch state transition.

    Fields are stringly-typed-by-design so the snapshot is self-
    contained — readers don't need to import any project modules to
    interpret an archived snapshot.
    """

    id: SnapshotId
    at: datetime
    state_from: KillSwitchState
    state_to: KillSwitchState
    trigger_code: str
    trigger_message: str
    severity: str
    # Free-form key/value bag for the rest: positions, pending
    # orders, equity, recent decisions, etc. Keeping it loose avoids
    # importing portfolio types into the safety layer.
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("AuditSnapshot.id must be non-empty")
        if not self.trigger_code:
            raise ValueError("AuditSnapshot.trigger_code must be non-empty")
        if self.severity not in ("DEGRADE", "KILL", "RECOVERY"):
            raise ValueError(
                f"AuditSnapshot.severity must be DEGRADE / KILL / RECOVERY, got {self.severity!r}"
            )


@runtime_checkable
class SnapshotSink(Protocol):
    """Storage contract for audit snapshots. Implementations SHALL
    persist atomically (or queue + retry) so a snapshot is never
    half-written."""

    def record(self, snapshot: AuditSnapshot) -> None: ...


@dataclass(slots=True)
class MemorySnapshotSink:
    """In-memory test double. The ``snapshots`` list mirrors what a
    file-backed sink would persist."""

    snapshots: list[AuditSnapshot] = field(default_factory=list)

    def record(self, snapshot: AuditSnapshot) -> None:
        self.snapshots.append(snapshot)


@dataclass(slots=True)
class FileSnapshotSink:
    """Append-only JSON-lines snapshot sink (one snapshot per line).

    The encoder serializes ``Decimal`` to its canonical string form
    so round-trips are lossless.
    """

    path: Path

    def record(self, snapshot: AuditSnapshot) -> None:
        line = json.dumps(_to_jsonable(snapshot), separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")


def _to_jsonable(snapshot: AuditSnapshot) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "at": snapshot.at.isoformat(),
        "state_from": snapshot.state_from.value,
        "state_to": snapshot.state_to.value,
        "trigger_code": snapshot.trigger_code,
        "trigger_message": snapshot.trigger_message,
        "severity": snapshot.severity,
        "payload": _payload_jsonable(snapshot.payload),
    }


def _payload_jsonable(payload: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, Mapping):
            out[k] = _payload_jsonable(v)
        else:
            out[k] = v
    return out
