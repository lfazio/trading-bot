"""Per-aggregate repositories — one read/write surface per concern.

REQ refs: REQ_F_PER_002, REQ_SDS_PER_002.
"""

from trading_system.persistence.repositories.portfolio import PortfolioRepository

__all__ = ["PortfolioRepository"]
