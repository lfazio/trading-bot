"""``EventClock`` — backtest clock that advances on tick consumption.

REQ refs:
- REQ_SDS_ARC_006 — time abstracted via the Clock interface; engines
  outside ``data/`` and ``dashboard/`` SHALL NOT call wall-clock
  primitives directly.
- REQ_F_BCT_001 / REQ_NF_DET_001 — backtest determinism; with the
  clock advanced explicitly, replay produces identical timestamp
  sequences.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class EventClock:
    """Backtest clock — explicitly advanced by the engine each tick.

    ``set(t)`` updates the current "now"; ``now()`` returns it.
    Reading before any ``set`` is a programmer error and panics.
    """

    _now: datetime | None = field(default=None)

    def set(self, t: datetime) -> None:
        self._now = t

    def now(self) -> datetime:
        assert self._now is not None, "EventClock.now() before any .set()"
        return self._now
