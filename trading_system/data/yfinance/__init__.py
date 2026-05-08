"""Yahoo Finance backtest historical-data adapter (CR-009).

Backtest-only concrete ``MarketDataProvider`` with a mandatory
on-disk cache. The cache is the system of record for replay
determinism (REQ_NF_DAT_001); the live ``yfinance`` library is
imported lazily only when a cache miss occurs *and*
``allow_network=True``.

Forbidden in live mode (REQ_F_DAT_009 / REQ_SDS_DAT_004) — the
provider's constructor panics if the system runs with
``run_mode == "live"``.

REQ refs: REQ_F_DAT_001..010, REQ_NF_DAT_001, REQ_SDS_DAT_001..004,
REQ_SDD_DAT_010..013.
"""

from trading_system.data.yfinance.cache import CacheKey, YFinanceCache
from trading_system.data.yfinance.mappers import bars_from_yf, dividends_from_yf
from trading_system.data.yfinance.symbols import yahoo_symbol_for

__all__ = [
    "CacheKey",
    "YFinanceCache",
    "bars_from_yf",
    "dividends_from_yf",
    "yahoo_symbol_for",
]
