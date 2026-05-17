"""Frozen rows: ``IndexFuturePosition`` + ``OverlayProposal`` +
``OverlayPositionState``.

REQ refs: REQ_F_HOV_003, REQ_F_HOV_005.

**No ``InstrumentClass.OVERLAY`` extension** — the existing
``models.instrument.InstrumentClass`` enum stays untouched. The
hedge-overlay subsystem keeps its own row types so consumers of the
broader enum don't have to know about overlay semantics. Structural
test ``test_structural.py`` greps ``models/instrument.py`` for an
``OVERLAY`` value and fails on match.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal


class OverlayPositionState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class IndexFuturePosition:
    """Row in the append-only ``OverlayLedger``.

    Invariants:
    - ``entry_index_level > 0``;
    - ``OPEN`` rows SHALL have ``exit_index_level is None`` and
      ``closed_at is None``;
    - ``CLOSED`` rows SHALL have BOTH ``exit_index_level`` AND
      ``closed_at`` set.
    """

    id: int
    benchmark: str
    notional: Decimal
    entry_index_level: Decimal
    entry_at: datetime
    state: OverlayPositionState = OverlayPositionState.OPEN
    exit_index_level: Decimal | None = None
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.entry_index_level <= 0:
            raise ValueError(
                f"hov:entry_index_non_positive: entry_index_level must be > 0, "
                f"got {self.entry_index_level}"
            )
        if not self.benchmark.strip():
            raise ValueError("hov:benchmark_empty")
        if self.state is OverlayPositionState.OPEN:
            if self.exit_index_level is not None or self.closed_at is not None:
                raise ValueError(
                    "hov:open_with_exit_fields: OPEN rows SHALL NOT carry "
                    "exit_index_level or closed_at"
                )
        else:  # CLOSED
            if self.exit_index_level is None or self.closed_at is None:
                raise ValueError(
                    "hov:closed_missing_exit_fields: CLOSED rows SHALL carry "
                    "both exit_index_level and closed_at"
                )
            if self.exit_index_level <= 0:
                raise ValueError(
                    f"hov:exit_index_non_positive: got {self.exit_index_level}"
                )


@dataclass(frozen=True, slots=True)
class OverlayProposal:
    """The sizer's output — at most one per call (REQ_F_HOV_003)."""

    benchmark: str
    side: Literal["short", "long"]
    notional: Decimal
    target_beta_delta: Decimal
    cadence: Literal["daily", "weekly", "monthly"]

    def __post_init__(self) -> None:
        if self.notional <= 0:
            raise ValueError(
                f"hov:proposal_notional_non_positive: got {self.notional}"
            )
        if not self.benchmark.strip():
            raise ValueError("hov:benchmark_empty")
