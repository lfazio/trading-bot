"""``Registry`` — versioned strategy store with immutable validated rows.

Per REQ_F_MTO_005 / REQ_SDS_CRS_003 every validated entry SHALL be
immutable; experimental entries can be replaced. Each entry carries
the (git_sha, config_hash, seed) triple needed for replay
determinism (REQ_NF_REP_001).

This file is the **only** member of ``strategy_lab/`` that runtime
modules SHALL import (REQ_SDS_MOD_014). Read-only access is via
``Registry.current()``, ``Registry.list_validated()``,
``Registry.get(strategy_id)``; mutation goes through ``store()``.

v1 backend: in-memory ``dict`` keyed by strategy id. When CR-008
(persistence / SQLite) lands as ``In-Progress``, the in-memory store
becomes a thin facade over a ``RegistryRepository`` keyed on
``(strategy_id, version)``; the public surface stays unchanged.

REQ refs:
- REQ_F_MTO_005 — versioned, immutable for validated entries.
- REQ_NF_REP_001 — replay reproducibility (sha + config_hash + seed).
- REQ_SDS_CRS_003 — entry shape: id, sha, config_hash, seed, metrics.
- REQ_SDD_DAT_004 — RegistryEntry shape rules.
- REQ_SDS_MOD_014 — runtime import boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_system.models.identifiers import StrategyId
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.strategy_lab.metrics import StrategyMetrics


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """One entry in the registry (REQ_SDD_DAT_004 / REQ_SDS_CRS_003).

    ``validated`` distinguishes accepted strategies (immutable) from
    experimental candidates (replaceable). ``metrics`` carries the
    full metric vector at acceptance time so consumers don't need
    to re-run a backtest to compare against.
    """

    strategy_id: StrategyId
    git_sha: str
    config_hash: str
    seed: int
    metrics: StrategyMetrics
    validated: bool
    created_at: datetime
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.git_sha:
            raise ValueError("RegistryEntry.git_sha must be non-empty")
        if not self.config_hash:
            raise ValueError("RegistryEntry.config_hash must be non-empty")


@dataclass(slots=True)
class Registry:
    """In-memory implementation of the registry surface.

    Public mutators: ``store``, ``mark_validated``.
    Public readers: ``get``, ``current``, ``list_validated``,
    ``list_experimental``.

    The runtime SHALL only call the readers (REQ_SDS_MOD_014).
    """

    _entries: dict[StrategyId, RegistryEntry] = field(default_factory=dict)
    _baseline_id: StrategyId | None = None

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def store(self, entry: RegistryEntry) -> Result[None, str]:
        """Add or replace ``entry`` in the registry.

        Validated entries are immutable: attempting to overwrite a
        validated row with another validated row under the same id
        returns ``Err("registry:validated_immutable:<id>")``.
        Experimental entries can be replaced by anything (validated
        or experimental).
        """
        existing = self._entries.get(entry.strategy_id)
        if existing is not None and existing.validated and entry.validated:
            return Err(f"registry:validated_immutable:{entry.strategy_id}")
        self._entries[entry.strategy_id] = entry
        return Ok(None)

    def mark_validated(self, strategy_id: StrategyId) -> Result[None, str]:
        """Promote an experimental entry to validated.

        Replaces the existing record with a copy that carries
        ``validated=True``; the entry's other fields are preserved
        (immutable identity by id; the registry never mutates a
        frozen dataclass).
        """
        existing = self._entries.get(strategy_id)
        if existing is None:
            return Err(f"registry:not_found:{strategy_id}")
        if existing.validated:
            return Err(f"registry:already_validated:{strategy_id}")
        promoted = RegistryEntry(
            strategy_id=existing.strategy_id,
            git_sha=existing.git_sha,
            config_hash=existing.config_hash,
            seed=existing.seed,
            metrics=existing.metrics,
            validated=True,
            created_at=existing.created_at,
            notes=existing.notes,
        )
        self._entries[strategy_id] = promoted
        return Ok(None)

    def set_baseline(self, strategy_id: StrategyId) -> Result[None, str]:
        """Pin the registry's "current best" reference.

        The optimizer compares new candidates against this baseline
        (REQ_F_MTO_006). Only validated entries can be the baseline.
        """
        existing = self._entries.get(strategy_id)
        if existing is None:
            return Err(f"registry:not_found:{strategy_id}")
        if not existing.validated:
            return Err(f"registry:not_validated:{strategy_id}")
        self._baseline_id = strategy_id
        return Ok(None)

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def get(self, strategy_id: StrategyId) -> Option[RegistryEntry]:
        entry = self._entries.get(strategy_id)
        return Some(entry) if entry is not None else Nothing()

    def current(self) -> Option[RegistryEntry]:
        """The pinned baseline entry, if any."""
        if self._baseline_id is None:
            return Nothing()
        entry = self._entries.get(self._baseline_id)
        return Some(entry) if entry is not None else Nothing()

    def list_validated(self) -> tuple[RegistryEntry, ...]:
        """All validated entries, sorted ascending by id (stable)."""
        return tuple(
            sorted(
                (e for e in self._entries.values() if e.validated),
                key=lambda e: str(e.strategy_id),
            )
        )

    def list_experimental(self) -> tuple[RegistryEntry, ...]:
        """All experimental (non-validated) entries, sorted by id."""
        return tuple(
            sorted(
                (e for e in self._entries.values() if not e.validated),
                key=lambda e: str(e.strategy_id),
            )
        )
