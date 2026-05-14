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
AccountId = NewType("AccountId", str)

# CR-006 / CR-008 — single-account sentinel. Every persisted row
# carries ``account_id``; the single-account default fills this
# value (REQ_F_PER_009 / REQ_SDD_PER_008).
DEFAULT_ACCOUNT_ID = AccountId("default")
