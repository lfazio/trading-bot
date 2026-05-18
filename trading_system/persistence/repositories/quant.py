"""``HypothesisRepository`` — CR-002 Phase B / REQ_SDD_QNT_007.

SQLite-backed implementation of the
``trading_system.strategy_lab.quant.library.HypothesisStore``
Protocol. Drop-in replacement for ``InMemoryHypothesisStore``
behind the same surface — the meta-loop wires either via
configuration.

Schema lives in ``persistence/migrations/0004_quant.sql``:

- ``hypotheses`` — one row per ``Hypothesis``; the initial
  ``state`` is recorded inline (typically PENDING at append).
- ``hypothesis_transitions`` — append-only audit log; the
  current state lookup is "latest transition for the id" with a
  fallback to ``initial_state`` (same semantics as the
  in-memory v1).

REQ refs:
- REQ_F_QNT_001 — three-state lifecycle persisted faithfully.
- REQ_NF_QNT_002 — deterministic iteration (sorted by
  ``created_at`` then id) so two runs against the same dataset
  see byte-identical results.
- REQ_F_PER_002 / REQ_F_PER_003 / REQ_F_PER_009 — repo per
  aggregate, explicit transactions, account_id-keyed.
- REQ_SDS_PER_002 — closed ``Err`` category set at the boundary.

The offline-only invariant (REQ_NF_QNT_001) holds at the
**import-graph** level: the runtime SHALL NOT import this module.
The persistence layer ships the table + the repo so operator
tooling can persist hypotheses out-of-band; the runtime tick
path remains free of any ``strategy_lab.quant`` reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
)
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.strategy_lab.quant.hypothesis import (
    DatasetWindow,
    Direction,
    Hypothesis,
    HypothesisId,
    HypothesisState,
)
from trading_system.strategy_lab.quant.library import TransitionRecord


@dataclass(slots=True)
class HypothesisRepository:
    """SQLite-backed ``HypothesisStore`` (CR-008 follow-up)."""

    conn: Connection

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def append(
        self,
        h: Hypothesis,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Insert one ``Hypothesis``. Duplicate ``(account_id,
        hypothesis_id)`` SHALL surface as
        ``Err("hypothesis:duplicate_id:<id>")`` (matches the
        in-memory v1's Err)."""
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                """
                INSERT INTO hypotheses (
                    account_id, hypothesis_id, claim,
                    falsification_criterion, metric, expected_direction,
                    operator_rationale, dataset_start, dataset_end,
                    dataset_frequency, initial_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(account_id),
                    str(h.id),
                    h.claim,
                    h.falsification_criterion,
                    h.metric,
                    h.expected_direction.value,
                    h.operator_rationale,
                    h.dataset_window.start.isoformat(),
                    h.dataset_window.end.isoformat(),
                    h.dataset_window.frequency,
                    h.state.value,
                    h.created_at.isoformat(),
                ),
            )
            self.conn.commit()
        except IntegrityError:
            self.conn.rollback()
            return Err(f"hypothesis:duplicate_id:{h.id}")
        except DatabaseError as e:
            self.conn.rollback()
            return Err(f"persistence:corrupt:hypotheses:write:{e}")
        return Ok(None)

    def record_transition(
        self,
        hypothesis_id: HypothesisId,
        new_state: HypothesisState,
        reason: str,
        at: datetime,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Append one ``hypothesis_transitions`` row. Missing
        hypothesis (no ``hypotheses`` row) surfaces as
        ``Err("hypothesis:not_found:<id>")`` matching the
        in-memory v1's contract."""
        # Check presence first so the FK-violation surface translates
        # to the documented Err category rather than a raw integrity
        # error.
        match self.get(hypothesis_id, account_id=account_id):
            case Err(reason_inner):
                return Err(reason_inner)
            case Ok(Nothing()):
                return Err(f"hypothesis:not_found:{hypothesis_id}")
            case Ok(Some(_)):
                pass
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                """
                INSERT INTO hypothesis_transitions (
                    account_id, hypothesis_id, transitioned_at,
                    new_state, reason
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(account_id),
                    str(hypothesis_id),
                    at.isoformat(),
                    new_state.value,
                    reason,
                ),
            )
            self.conn.commit()
        except IntegrityError as e:
            self.conn.rollback()
            return Err(
                f"persistence:integrity:hypothesis_transitions:duplicate:{hypothesis_id}@{at.isoformat()}: {e}"
            )
        except DatabaseError as e:
            self.conn.rollback()
            return Err(f"persistence:corrupt:hypothesis_transitions:write:{e}")
        return Ok(None)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(
        self,
        hypothesis_id: HypothesisId,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[Option[Hypothesis], str]:
        try:
            cursor = self.conn.execute(
                "SELECT * FROM hypotheses "
                "WHERE account_id = ? AND hypothesis_id = ?",
                (str(account_id), str(hypothesis_id)),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:hypotheses:read:{e}")
        if row is None:
            return Ok(Nothing())
        return Ok(Some(_row_to_hypothesis(dict(row))))

    def list_all(
        self,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[tuple[Hypothesis, ...], str]:
        """Return every persisted hypothesis sorted by
        ``(created_at, hypothesis_id)`` so REQ_NF_QNT_002
        determinism holds across runs."""
        try:
            cursor = self.conn.execute(
                "SELECT * FROM hypotheses "
                "WHERE account_id = ? "
                "ORDER BY created_at ASC, hypothesis_id ASC",
                (str(account_id),),
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:hypotheses:read:{e}")
        return Ok(tuple(_row_to_hypothesis(dict(r)) for r in rows))

    def current_state(
        self,
        hypothesis_id: HypothesisId,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[Option[HypothesisState], str]:
        """Latest transition's state, falling back to the row's
        ``initial_state`` when none were recorded yet. Mirrors
        ``InMemoryHypothesisStore.current_state``."""
        match self.get(hypothesis_id, account_id=account_id):
            case Err(reason):
                return Err(reason)
            case Ok(Nothing()):
                return Ok(Nothing())
            case Ok(Some(h)):
                row = h
        try:
            cursor = self.conn.execute(
                """
                SELECT new_state FROM hypothesis_transitions
                WHERE account_id = ? AND hypothesis_id = ?
                ORDER BY transitioned_at DESC
                LIMIT 1
                """,
                (str(account_id), str(hypothesis_id)),
            )
            t_row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:hypothesis_transitions:read:{e}")
        if t_row is None:
            return Ok(Some(row.state))
        return Ok(Some(HypothesisState(t_row["new_state"])))

    def transitions_for(
        self,
        hypothesis_id: HypothesisId,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[tuple[TransitionRecord, ...], str]:
        try:
            cursor = self.conn.execute(
                """
                SELECT * FROM hypothesis_transitions
                WHERE account_id = ? AND hypothesis_id = ?
                ORDER BY transitioned_at ASC
                """,
                (str(account_id), str(hypothesis_id)),
            )
            rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:hypothesis_transitions:read:{e}")
        return Ok(
            tuple(
                TransitionRecord(
                    hypothesis_id=HypothesisId(r["hypothesis_id"]),
                    new_state=HypothesisState(r["new_state"]),
                    reason=r["reason"],
                    transitioned_at=datetime.fromisoformat(r["transitioned_at"]),
                )
                for r in rows
            )
        )


def _row_to_hypothesis(row: dict[str, object]) -> Hypothesis:
    """Reconstruct a ``Hypothesis`` from a persisted row."""
    return Hypothesis(
        id=HypothesisId(str(row["hypothesis_id"])),
        claim=str(row["claim"]),
        falsification_criterion=str(row["falsification_criterion"]),
        dataset_window=DatasetWindow(
            start=datetime.fromisoformat(str(row["dataset_start"])),
            end=datetime.fromisoformat(str(row["dataset_end"])),
            frequency=str(row["dataset_frequency"]),
        ),
        metric=str(row["metric"]),
        expected_direction=Direction(str(row["expected_direction"])),
        operator_rationale=str(row["operator_rationale"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        state=HypothesisState(str(row["initial_state"])),
    )
