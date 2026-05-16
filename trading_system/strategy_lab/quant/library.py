"""``HypothesisLibrary`` — append-only registry of hypotheses.

V1 ships an in-memory backend (``InMemoryHypothesisStore``); the
CR-008 follow-up adds a SQLite-backed ``HypothesisRepository`` that
satisfies the same Protocol. The library never overwrites a stored
hypothesis — state transitions are recorded as separate audit rows
so the original PENDING row stays canonical.

REQ refs: REQ_F_QNT_001..003, REQ_SDS_QNT_003, REQ_SDD_QNT_003.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.strategy_lab.quant.hypothesis import (
    Hypothesis,
    HypothesisId,
    HypothesisState,
)


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """Audit row recorded each time a hypothesis changes state.

    The original Hypothesis is immutable; this row is the canonical
    "what happened" trail consumed by the operator notification
    fan-out (CR-001) and the ImprovementReport that links each
    shipped strategy back to its supporting hypotheses
    (REQ_F_QNT_005).
    """

    hypothesis_id: HypothesisId
    new_state: HypothesisState
    reason: str
    transitioned_at: datetime


@runtime_checkable
class HypothesisStore(Protocol):
    """Storage surface shared by the in-memory v1 and the CR-008
    SQLite repository (Phase-B follow-up)."""

    def append(self, h: Hypothesis) -> Result[None, str]: ...
    def get(self, hypothesis_id: HypothesisId) -> Result[Option[Hypothesis], str]: ...
    def list_all(self) -> Result[tuple[Hypothesis, ...], str]: ...
    def current_state(
        self, hypothesis_id: HypothesisId
    ) -> Result[Option[HypothesisState], str]: ...
    def record_transition(
        self,
        hypothesis_id: HypothesisId,
        new_state: HypothesisState,
        reason: str,
        at: datetime,
    ) -> Result[None, str]: ...
    def transitions_for(
        self, hypothesis_id: HypothesisId
    ) -> Result[tuple[TransitionRecord, ...], str]: ...


@dataclass(slots=True)
class InMemoryHypothesisStore:
    """In-memory backend for v1.

    Hypotheses are stored in a single mutable dict; transitions live
    in an append-only list. Iteration is sorted by ``created_at`` so
    determinism contracts hold (REQ_NF_QNT_002).
    """

    _hypotheses: dict[HypothesisId, Hypothesis] = field(default_factory=dict)
    _transitions: list[TransitionRecord] = field(default_factory=list)

    def append(self, h: Hypothesis) -> Result[None, str]:
        if h.id in self._hypotheses:
            return Err(f"hypothesis:duplicate_id:{h.id}")
        self._hypotheses[h.id] = h
        return Ok(None)

    def get(self, hypothesis_id: HypothesisId) -> Result[Option[Hypothesis], str]:
        existing = self._hypotheses.get(hypothesis_id)
        if existing is None:
            return Ok(Nothing())
        return Ok(Some(existing))

    def list_all(self) -> Result[tuple[Hypothesis, ...], str]:
        return Ok(_sorted_by_created_at(self._hypotheses.values()))

    def current_state(
        self, hypothesis_id: HypothesisId
    ) -> Result[Option[HypothesisState], str]:
        if hypothesis_id not in self._hypotheses:
            return Ok(Nothing())
        # Latest transition wins; fall back to the original state.
        latest_state: HypothesisState | None = None
        latest_at: datetime | None = None
        for t in self._transitions:
            if t.hypothesis_id == hypothesis_id:
                if latest_at is None or t.transitioned_at >= latest_at:
                    latest_at = t.transitioned_at
                    latest_state = t.new_state
        if latest_state is None:
            return Ok(Some(self._hypotheses[hypothesis_id].state))
        return Ok(Some(latest_state))

    def record_transition(
        self,
        hypothesis_id: HypothesisId,
        new_state: HypothesisState,
        reason: str,
        at: datetime,
    ) -> Result[None, str]:
        if hypothesis_id not in self._hypotheses:
            return Err(f"hypothesis:not_found:{hypothesis_id}")
        self._transitions.append(
            TransitionRecord(
                hypothesis_id=hypothesis_id,
                new_state=new_state,
                reason=reason,
                transitioned_at=at,
            )
        )
        return Ok(None)

    def transitions_for(
        self, hypothesis_id: HypothesisId
    ) -> Result[tuple[TransitionRecord, ...], str]:
        rows = tuple(
            t for t in self._transitions if t.hypothesis_id == hypothesis_id
        )
        return Ok(rows)


def _sorted_by_created_at(
    items: Iterable[Hypothesis],
) -> tuple[Hypothesis, ...]:
    return tuple(sorted(items, key=lambda h: (h.created_at, str(h.id))))


@dataclass(slots=True)
class HypothesisLibrary:
    """Public surface — operators and the runner go through this
    type, not the underlying store."""

    store: HypothesisStore

    def store_pending(self, h: Hypothesis) -> Result[None, str]:
        """Insert a brand-new hypothesis. Only PENDING insertions are
        allowed at this entry point (REQ_SDD_QNT_003); a hypothesis
        that arrives in a non-PENDING state is a programmer error."""
        if h.state is not HypothesisState.PENDING:
            return Err(f"hypothesis:bad_initial_state:{h.state}")
        return self.store.append(h)

    def transition(
        self,
        hypothesis_id: HypothesisId,
        new_state: HypothesisState,
        reason: str,
        *,
        at: datetime,
    ) -> Result[None, str]:
        if new_state is HypothesisState.PENDING:
            return Err("hypothesis:bad_transition:cannot_revert_to_pending")
        if not reason.strip():
            return Err("hypothesis:bad_transition:empty_reason")
        # Block transition when the hypothesis is unknown.
        state_lookup = self.store.current_state(hypothesis_id)
        match state_lookup:
            case Err(reason_str):
                return Err(reason_str)
            case Ok(Nothing()):
                return Err(f"hypothesis:not_found:{hypothesis_id}")
            case _:
                pass
        return self.store.record_transition(
            hypothesis_id, new_state, reason, at
        )

    def get(self, hypothesis_id: HypothesisId) -> Result[Option[Hypothesis], str]:
        return self.store.get(hypothesis_id)

    def list_by_state(
        self, state: HypothesisState
    ) -> Result[tuple[Hypothesis, ...], str]:
        all_h = self.store.list_all()
        match all_h:
            case Err(reason):
                return Err(reason)
            case Ok(items):
                pass
        out: list[Hypothesis] = []
        for h in items:
            cs = self.store.current_state(h.id)
            match cs:
                case Err(reason):
                    return Err(reason)
                case Ok(Some(s)):
                    if s is state:
                        out.append(h)
                case _:
                    pass
        return Ok(tuple(out))

    def transitions_for(
        self, hypothesis_id: HypothesisId
    ) -> Result[tuple[TransitionRecord, ...], str]:
        return self.store.transitions_for(hypothesis_id)
