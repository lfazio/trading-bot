"""``Hypothesis`` + supporting types — REQ_F_QNT_001 / REQ_SDS_QNT_001.

Every shape here is frozen + slotted so the meta-loop's
deterministic replay (REQ_NF_QNT_002) holds: identical inputs
produce identical Hypothesis rows + identical HypothesisResult
rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType


HypothesisId = NewType("HypothesisId", str)


class HypothesisState(StrEnum):
    """Closed three-state lifecycle (REQ_F_QNT_001)."""

    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"


class Direction(StrEnum):
    """Expected direction of the metric movement under the claim."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    TWO_TAILED = "two_tailed"


# Metric names the validator (gate 4) recognises as legal. Hypotheses
# can target any of these; the validator rejects everything else with
# ``hypothesis:metric_mismatch:unknown_metric:<name>``. Operators
# extend the set through a new SRS amendment + a corresponding
# StrategyMetrics column.
DEFAULT_METRIC_VOCABULARY: frozenset[str] = frozenset(
    {
        "sharpe",
        "adjusted_sharpe",
        "net_after_tax_return",
        "max_drawdown",
        "information_coefficient",
        "stability",
        "turnover",
    }
)


@dataclass(frozen=True, slots=True)
class DatasetWindow:
    """``[start, end)`` window the hypothesis is tested on."""

    start: datetime
    end: datetime
    frequency: str  # e.g. "1d", "1h", "5m"

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(
                f"DatasetWindow.end must be > start; got {self.end} <= {self.start}"
            )
        if not self.frequency.strip():
            raise ValueError("DatasetWindow.frequency must be non-empty")

    def duration_days(self) -> int:
        delta = self.end - self.start
        return delta.days


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """An operator-readable claim with a falsification criterion.

    The ``__post_init__`` invariants are checked at construction so
    the validator can rely on the frozen-dataclass contract for the
    structural gate (gate 1) without re-validating every field.
    """

    id: HypothesisId
    claim: str
    falsification_criterion: str
    dataset_window: DatasetWindow
    metric: str
    expected_direction: Direction
    operator_rationale: str
    created_at: datetime
    state: HypothesisState = HypothesisState.PENDING

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValueError("Hypothesis.id must be non-empty")
        for name in (
            "claim",
            "falsification_criterion",
            "metric",
            "operator_rationale",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"Hypothesis.{name} must be non-empty")
        # state is already typed; reject raw strings that smuggled in
        # via a non-StrEnum path (e.g., JSON deserialisation upstream).
        if not isinstance(self.state, HypothesisState):
            raise TypeError(
                f"Hypothesis.state must be a HypothesisState, "
                f"got {type(self.state).__name__}"
            )


@dataclass(frozen=True, slots=True)
class HypothesisResult:
    """Terminal outcome of a HypothesisRunner cycle.

    ``outcome`` is always VALIDATED or REJECTED — PENDING is the
    pre-decision state and never appears here. ``rejection_reason``
    is non-empty when ``outcome == REJECTED`` and empty otherwise
    (enforced at construction so consumers can pattern-match on the
    pair).
    """

    hypothesis_id: HypothesisId
    outcome: HypothesisState
    confidence_band: tuple[Decimal, Decimal]
    decided_at: datetime
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        if self.outcome is HypothesisState.PENDING:
            raise ValueError(
                "HypothesisResult.outcome must be VALIDATED or REJECTED, "
                "not PENDING"
            )
        if self.outcome is HypothesisState.VALIDATED and self.rejection_reason:
            raise ValueError(
                "HypothesisResult.rejection_reason must be empty when "
                "outcome=VALIDATED"
            )
        if self.outcome is HypothesisState.REJECTED and not self.rejection_reason.strip():
            raise ValueError(
                "HypothesisResult.rejection_reason must be non-empty when "
                "outcome=REJECTED"
            )
        lo, hi = self.confidence_band
        if lo > hi:
            raise ValueError(
                f"HypothesisResult.confidence_band lower ({lo}) must "
                f"be <= upper ({hi})"
            )
