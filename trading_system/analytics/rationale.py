"""Trade-rationale lookup over ``BacktestResult.rationales`` (CR-015).

This is the public read surface (REQ_SDS_RAT_001) — consumers go
through ``rationale_for`` rather than reaching into
``BacktestResult.rationales`` directly so the lookup stays stable
across persistence transitions (in-memory v1 → CR-008's
``TradeRationaleRepository`` live-mode).

REQ refs: REQ_F_RAT_001, REQ_F_RAT_004, REQ_SDS_RAT_001,
REQ_SDD_RAT_003.
"""

from __future__ import annotations

from trading_system.backtesting.result import BacktestResult
from trading_system.models.identifiers import TradeId
from trading_system.models.rationale import TradeRationale
from trading_system.result import Nothing, Option, Some


def rationale_for(
    result: BacktestResult, trade_id: TradeId
) -> Option[TradeRationale]:
    """Linear scan over ``result.rationales`` returning the first match
    by ``trade_id``. ``Nothing()`` when absent (REQ_SDD_RAT_003 —
    O(n) is sufficient for v1; the live-mode repository indexes by
    trade_id)."""
    for r in result.rationales:
        if r.trade_id == trade_id:
            return Some(r)
    return Nothing()
