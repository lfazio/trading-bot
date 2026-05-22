"""Webapp-side runtime modes (paper trading, etc.).

The webapp ships **runtime wrappers** that drive the existing
engine pieces (``LocalBrokerAdapter``, ``Portfolio``, ``Backtest``-
style tick loop) against live market data. These are NOT new
``BrokerAdapter`` concrete classes — the CR-019 paper-trading
mode preserves the REQ_F_BRK_003 "live adapters deferred"
discipline by wrapping the existing simulation surface with a
live data source.

REQ refs:
- REQ_F_PAP_001..005 — paper-trading runtime contract.
- REQ_SDS_WEB2_004 — runtime types.
- REQ_SDD_WEB2_003..005 — class layout + tick / resume rules.
"""

from __future__ import annotations

from trading_system.webapp.runtimes.paper_trading import (
    PaperTradingRuntime,
    PaperTradingSession,
    RuntimeRegistry,
)

__all__ = [
    "PaperTradingRuntime",
    "PaperTradingSession",
    "RuntimeRegistry",
]
