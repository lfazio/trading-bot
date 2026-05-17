"""Summary JSON renderer — the machine-readable dashboard payload
(REQ_F_RPT_001).

Reuses ``notifications.canonical.canonical_json_line`` so the
output is byte-identical for identical inputs (REQ_NF_RPT_001).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_system.backtesting.result import BacktestResult
from trading_system.notifications.canonical import canonical_json_line


def build_summary(result: BacktestResult) -> dict[str, Any]:
    """Build the dashboard-shaped summary dict.

    Same overall shape the existing ``dashboard.Dashboard.render``
    surfaces — kept distinct here so the report directory is
    self-describing without consumers needing to construct a
    Dashboard.
    """
    final_equity = result.final_equity_after_tax
    final_currency = final_equity.currency.value
    return {
        "trades_count": len(result.trades),
        "knockouts": result.knockouts,
        "injections_applied": result.injections_applied,
        "currency": final_currency,
        "final_cash": result.final_cash.amount,
        "final_equity_after_tax": final_equity.amount,
        "realized_gross": result.realized_gross.amount,
        "realized_after_tax": result.realized_after_tax.amount,
        "dividends_gross": result.dividends_gross.amount,
        "dividends_after_tax": result.dividends_after_tax.amount,
        "max_drawdown": _max_drawdown(result),
        "equity_curve_points": len(result.equity_curve),
    }


def render_summary_json(result: BacktestResult) -> str:
    """Return the canonical-JSON one-liner. REQ_NF_RPT_001 byte-
    identical replay is preserved by the upstream canonical writer."""
    return canonical_json_line(build_summary(result))


def _max_drawdown(result: BacktestResult) -> Decimal:
    """Max drawdown across the equity_after_tax curve, as a positive
    decimal in [0, 1]. Empty curve ⇒ 0."""
    if not result.equity_curve:
        return Decimal("0")
    return max(p.drawdown_pct for p in result.equity_curve)
