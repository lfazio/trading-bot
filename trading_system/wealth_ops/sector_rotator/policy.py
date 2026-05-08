"""``RotationPolicy`` (frozen) + ``HoldingState`` (single-writer cursor).

Per REQ_SDS_SCT_003 / REQ_SDD_SCT_005, the rotator owns the only
write reference to ``HoldingState``; outside readers receive
defensive copies. The cursor's ``quarter_started_at`` is reset on
quarter rollover (REQ_SDD_SCT_005) so ``rotations_this_quarter``
resets together.

REQ refs: REQ_F_SCT_003, REQ_F_SCT_004, REQ_F_SCT_006,
REQ_SDS_SCT_003, REQ_SDD_SCT_005, REQ_SDD_SCT_006, REQ_SDD_SCT_007.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_system.models.phase import MarketRegime

# REQ_SDD_SCT_007 default: 60 days minimum holding before exit.
_DEFAULT_MIN_HOLDING_DAYS = 60
# REQ_F_SCT_006 default: at most one full rotation per quarter.
_DEFAULT_MAX_ROTATIONS_PER_QUARTER = 1
# REQ_F_SCT_004 default: at most one direction-change per regime episode.
_DEFAULT_WHIPSAW_DAMPENER = 1


@dataclass(frozen=True, slots=True)
class RotationPolicy:
    """Frozen knobs for the rotator (REQ_SDS_INT_004)."""

    min_holding_days: int = _DEFAULT_MIN_HOLDING_DAYS
    max_rotations_per_quarter: int = _DEFAULT_MAX_ROTATIONS_PER_QUARTER
    whipsaw_dampener: int = _DEFAULT_WHIPSAW_DAMPENER

    def __post_init__(self) -> None:
        if self.min_holding_days < 0:
            raise ValueError(
                f"RotationPolicy.min_holding_days must be >= 0, got {self.min_holding_days}"
            )
        if self.max_rotations_per_quarter < 0:
            raise ValueError(
                f"RotationPolicy.max_rotations_per_quarter must be >= 0, "
                f"got {self.max_rotations_per_quarter}"
            )
        if self.whipsaw_dampener < 0:
            raise ValueError(
                f"RotationPolicy.whipsaw_dampener must be >= 0, got {self.whipsaw_dampener}"
            )


@dataclass(slots=True)
class HoldingState:
    """Single mutable cursor for the rotator (REQ_SDS_SCT_003).

    - ``last_entry`` / ``last_exit`` track per-sector timestamps
      for the holding-period guard (REQ_SDD_SCT_007).
    - ``rotations_this_quarter`` + ``quarter_started_at`` enforce
      REQ_F_SCT_006 with a quarter-rollover reset.
    - ``regime_episode`` + ``direction_changes_in_episode`` drive
      the whipsaw dampener; crossing into a new regime resets both
      (REQ_SDD_SCT_006).
    """

    last_entry: dict[str, datetime] = field(default_factory=dict)
    last_exit: dict[str, datetime] = field(default_factory=dict)
    rotations_this_quarter: int = 0
    quarter_started_at: datetime | None = None
    regime_episode: tuple[MarketRegime, datetime] | None = None
    direction_changes_in_episode: int = 0

    def snapshot(self) -> HoldingState:
        """Return a defensive copy so external readers cannot mutate
        the live cursor (REQ_SDS_SCT_003)."""
        return HoldingState(
            last_entry=dict(self.last_entry),
            last_exit=dict(self.last_exit),
            rotations_this_quarter=self.rotations_this_quarter,
            quarter_started_at=self.quarter_started_at,
            regime_episode=self.regime_episode,
            direction_changes_in_episode=self.direction_changes_in_episode,
        )
