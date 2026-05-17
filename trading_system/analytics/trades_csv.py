"""Trade-log CSV renderer — REQ_F_RPT_002.

Pure function over the ``BacktestResult.trades`` + ``rationales``
tuples. Output:
- UTF-8, comma-separated.
- ISO-8601 datetimes (with timezone offset).
- ``Decimal``-as-TEXT — no float intermediates (REQ_F_PER_005 family).
- Chronological sort by ``(executed_at, trade_id)`` so two runs with
  identical inputs produce identical row sequences (REQ_NF_RPT_001).
- Empty trades tuple ⇒ header-only output.

The v1 column set reflects what ``Trade`` actually carries (plus
``strategy_id`` / ``strategy_version`` joined via the linked
``TradeRationale`` when present). The SDD §11.18 originally listed
``instrument_id`` / ``side`` / ``gross_pnl`` / ``net_pnl`` as
columns — those rely on joining the order log + a P&L
attribution pass and land in a Phase-B CSV-schema-bump (the
``manifest.json``'s ``report_schema_version`` field is the version
gate per REQ_F_RPT_003).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping

from trading_system.models.identifiers import TradeId
from trading_system.models.rationale import TradeRationale
from trading_system.models.trading import Trade


_HEADER: tuple[str, ...] = (
    "at",
    "trade_id",
    "order_id",
    "price",
    "quantity_filled",
    "fees",
    "fees_currency",
    "slippage",
    "strategy_id",
    "strategy_version",
)


def render_trades_csv(
    trades: tuple[Trade, ...],
    rationales: tuple[TradeRationale, ...] = (),
) -> str:
    """REQ_F_RPT_002 — chronologically-sorted UTF-8 CSV.

    ``rationales`` SHALL be aligned to ``trades`` (one-for-one per
    REQ_F_RAT_004) when non-empty. The renderer joins by
    ``trade_id`` so a partial rationale list (some trades have
    rationales, others don't) is still safe.
    """
    rationales_by_trade: Mapping[TradeId, TradeRationale] = {
        r.trade_id: r for r in rationales
    }
    rows = sorted(trades, key=lambda t: (t.executed_at, str(t.id)))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(_HEADER)
    for t in rows:
        rationale = rationales_by_trade.get(t.id)
        w.writerow(
            [
                t.executed_at.isoformat(),
                str(t.id),
                str(t.order_id),
                str(t.price),
                str(t.quantity_filled),
                str(t.fees.amount),
                str(t.fees.currency.value),
                str(t.slippage),
                str(rationale.strategy_id) if rationale else "",
                rationale.strategy_version if rationale else "",
            ]
        )
    return buf.getvalue()
