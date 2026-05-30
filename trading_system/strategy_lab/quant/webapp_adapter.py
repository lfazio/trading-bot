"""CR-027 — webapp ↔ quant-layer adapter.

Implements the ``HypothesisFilerView`` + ``HypothesisListerView``
Protocols the webapp routes consume on ``app.state``. The webapp
SHALL NOT import from ``strategy_lab/quant/`` directly
(REQ_NF_QNT_001 + REQ_SDD_FAS_001 — `strategy_lab/quant/` stays
offline-only); the operator wires an instance of this adapter at
boot time so the route surface stays Protocol-shaped.

Construction details:
- ``HypothesisValidator`` runs inline on every ``file(...)`` call.
- ``HypothesisRepository`` persists the row on Ok.
- Rejected hypotheses surface as ``Err("hypothesis:*")`` per the
  closed Err set (REQ_F_QNT_004) — the webapp route maps these
  to ``400 Bad Request``.

REQ refs:
- REQ_SDD_QNT_010 — POST /api/hypotheses delegates here.
- REQ_SDD_QNT_011 — GET  /api/hypotheses paginated read.
- REQ_F_QNT_004   — 5-gate Validator runs inline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok, Result
from trading_system.strategy_lab.quant.hypothesis import (
    DatasetWindow,
    Direction,
    Hypothesis,
    HypothesisId,
    HypothesisState,
)
from trading_system.strategy_lab.quant.validator import HypothesisValidator


@runtime_checkable
class _RepositorySlot(Protocol):
    """Subset of the CR-008 ``HypothesisRepository`` the adapter
    needs. Defined as a Protocol so this module avoids importing
    ``trading_system.persistence`` — strategy_lab/quant/ structural
    audit forbids that reach."""

    def append(self, h: Hypothesis, *, account_id: AccountId): ...

    def list_all(self, *, account_id: AccountId): ...


@dataclass(slots=True)
class StrategyLabHypothesisFiler:
    """Concrete ``HypothesisFilerView`` — construct + validate + persist.

    The webapp wires an instance of this dataclass at boot under
    ``app.state.hypothesis_filer``. Tests inject a duck-typed fake.
    """

    validator: HypothesisValidator
    repository: _RepositorySlot

    def file(
        self, *, payload: dict[str, Any], account_id: AccountId
    ) -> Result[dict[str, Any], str]:
        """Build a ``Hypothesis`` from the validated Pydantic payload,
        run the 5-gate Validator, and persist on Ok.

        Returns ``Ok({"id", "state", "validated"})`` on success;
        ``Err("hypothesis:*")`` on Validator rejection;
        ``Err("persistence:*")`` on repository failure.
        """
        now = datetime.now(tz=UTC)
        hypothesis_id = HypothesisId(
            f"hyp-{now.isoformat()}-{uuid4().hex[:8]}"
        )

        try:
            direction = Direction(payload["expected_direction"])
        except (KeyError, ValueError):
            return Err(
                f"hypothesis:bad_expected_direction:"
                f"{payload.get('expected_direction')!r}"
            )

        try:
            window = DatasetWindow(
                start=_parse_datetime(payload["dataset_window"]["start"]),
                end=_parse_datetime(payload["dataset_window"]["end"]),
                frequency=str(payload["dataset_window"]["frequency"]),
            )
        except (KeyError, ValueError, TypeError) as e:
            return Err(f"hypothesis:structural:dataset_window:{e!s}")

        try:
            h = Hypothesis(
                id=hypothesis_id,
                claim=str(payload.get("claim", "")),
                falsification_criterion=str(
                    payload.get("falsification_criterion", "")
                ),
                dataset_window=window,
                metric=str(payload.get("metric", "")),
                expected_direction=direction,
                operator_rationale=str(payload.get("operator_rationale", "")),
                created_at=now,
                state=HypothesisState.PENDING,
            )
        except ValueError as e:
            # __post_init__ rejected the structural invariants.
            return Err(f"hypothesis:structural:{e!s}")

        validate_result = self.validator.validate(h)
        if isinstance(validate_result, Err):
            return Err(validate_result.error)

        persist_result = self.repository.append(h, account_id=account_id)
        if isinstance(persist_result, Err):
            return Err(persist_result.error)

        return Ok(
            {
                "id": str(h.id),
                "state": h.state.value.upper(),
                "validated": False,
            }
        )


@dataclass(slots=True)
class StrategyLabHypothesisLister:
    """Concrete ``HypothesisListerView`` — paginated read returning
    plain dicts so the webapp route stays Protocol-shaped."""

    repository: _RepositorySlot

    def list_filed(
        self, *, account_id: AccountId
    ) -> Result[tuple[dict[str, Any], ...], str]:
        result = self.repository.list_all(account_id=account_id)
        if isinstance(result, Err):
            return Err(result.error)
        rows = tuple(_hypothesis_to_dict(h) for h in result.value)
        return Ok(rows)


def _parse_datetime(raw: object) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        raise TypeError(f"expected str or datetime, got {type(raw).__name__}")
    # Pydantic emits ISO-8601 strings; ``fromisoformat`` handles
    # trailing-Z timezone via the 3.11+ stdlib improvement.
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _hypothesis_to_dict(h: Hypothesis) -> dict[str, Any]:
    """Canonical Hypothesis-as-dict serialiser. Used by the lister
    + the webapp's GET routes. Keys are sorted by the JSON canon at
    the response boundary (REQ_NF_WEB_002)."""
    return {
        "id": str(h.id),
        "claim": h.claim,
        "falsification_criterion": h.falsification_criterion,
        "metric": h.metric,
        "expected_direction": h.expected_direction.value,
        "operator_rationale": h.operator_rationale,
        "dataset_window": {
            "start": h.dataset_window.start.isoformat(),
            "end": h.dataset_window.end.isoformat(),
            "frequency": h.dataset_window.frequency,
        },
        "state": h.state.value,
        "created_at": h.created_at.isoformat(),
    }
