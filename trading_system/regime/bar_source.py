"""``BarSource`` Protocol — pluggable benchmark-index bar provider.

The detector reads OHLCV bars from a benchmark index. v1 ships a
synthetic "EU equity composite" derived from the screener's universe
(no extra data dependency); live feeds plug in here when CR-009's
cache is operator-pinned to a real index symbol.

REQ refs: REQ_F_RGM_006, REQ_SDS_RGM_002.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from trading_system.data.types import Bar


@runtime_checkable
class BarSource(Protocol):
    """Return the most recent ``window`` bars ending at ``end``.

    Implementations SHALL be deterministic for replay
    (REQ_NF_RGM_001) — identical ``(end, window)`` inputs against an
    identical underlying data state SHALL produce identical bar
    sequences.
    """

    def bars(self, *, end: datetime, window: int) -> Sequence[Bar]:
        ...
