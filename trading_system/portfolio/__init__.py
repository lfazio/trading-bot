"""Portfolio: cash, positions, realized PnL, dividends, equity curve.

Implements the ``PortfolioView`` Protocol (``trading_system/strategies/
protocol.py``) so the risk engine and strategies can read state without
touching mutating methods.

REQ refs: REQ_F_PRT_001 (after-tax equity is canonical),
REQ_F_PRT_003 (exposure_pct per allocation bucket),
REQ_SDS_MOD_011, REQ_SDD_DAT_005 (executed fees on Trade.fees).
"""

from trading_system.portfolio.portfolio import Portfolio

__all__ = ["Portfolio"]
