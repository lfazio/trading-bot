"""Domain identifier types.

Each ID is a distinct ``NewType`` over ``str`` so that the type checker
rejects cross-type assignment (e.g., passing a ``TradeId`` where an
``OrderId`` is expected) — REQ_SDD_TYP_002.

Verified statically by ``mypy --strict`` (REQ_NF_TRC_001).
"""

from __future__ import annotations

from typing import NewType

OrderId = NewType("OrderId", str)
TradeId = NewType("TradeId", str)
InstrumentId = NewType("InstrumentId", str)
StrategyId = NewType("StrategyId", str)
SnapshotId = NewType("SnapshotId", str)
