"""CSV-seeded fundamentals provider — CR-014 Phase-5 implementation.

Sits at L2 inside ``data/`` alongside ``data/yfinance/`` (which deferred
fundamentals per REQ_F_DAT_010). The screener consumes fundamentals
through the existing ``MarketDataProvider.fundamentals(instr)``
Protocol — CR-014 adds no new boundary.

Public surface:

- ``CSVFundamentalsProvider`` — loads ``data/seed_fundamentals.csv`` at
  construction; serves ``fundamentals()`` only; other Protocol methods
  return ``Err("data:not_supported:csv_only")`` so a mis-wired caller
  fails fast (REQ_F_FND_001).
- ``CompositeFundamentalsProvider`` — chains 2+ providers; first ``Ok``
  wins; if every delegate fails, returns the LAST ``Err`` so the
  operator sees the most-specific error (REQ_F_FND_004).
- ``FundamentalsConfig`` — frozen parameters loaded from
  ``config/fundamentals.yaml`` with documented defaults.

REQ refs: REQ_F_FND_001..005, REQ_NF_FND_001, REQ_SDS_FND_001,
REQ_SDD_FND_001..003.
"""

from trading_system.data.fundamentals.composite import CompositeFundamentalsProvider
from trading_system.data.fundamentals.config import FundamentalsConfig
from trading_system.data.fundamentals.csv_provider import (
    CSVFundamentalsProvider,
    CsvLoadError,
)

__all__ = [
    "CSVFundamentalsProvider",
    "CompositeFundamentalsProvider",
    "CsvLoadError",
    "FundamentalsConfig",
]
