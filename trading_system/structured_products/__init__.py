"""Structured-product admission engine — decompose-or-reject.

REQ refs:
- REQ_F_STP_001 — total SP allocation <= 10% of portfolio.
- REQ_F_STP_002 — every product MUST be decomposable; otherwise reject.
- REQ_F_STP_003 — admit only in low-vol / sideways / stable-macro
  (BULL or SIDEWAYS in our regime taxonomy).
- REQ_F_STP_004 — block in HIGH_VOL / BEAR / liquidity-stress.
- REQ_F_STP_005 — every candidate passes stress (crash, vol, corr).
- REQ_F_STP_006 — issuer concentration cap (25% of SP allocation).
- REQ_F_STP_007 — no SP / turbo stack on the same underlying.
- REQ_SDS_MOD_008 — non-decomposable products rejected before
  allocation logic.
- REQ_SDD_ALG_012 — decomposer table per payoff type.
- REQ_SDD_ALG_013 — stress scenarios (-20% crash, vol x3, corr -> 1).
- REQ_SDD_ALG_014 — issuer-concentration constant.
"""

from trading_system.structured_products.admission import (
    AdmissionConfig,
    admit,
)
from trading_system.structured_products.decomposers import PAYOFF_DECOMPOSERS
from trading_system.structured_products.decomposition import Decomposition
from trading_system.structured_products.stress import stress_pass

__all__ = [
    "PAYOFF_DECOMPOSERS",
    "AdmissionConfig",
    "Decomposition",
    "admit",
    "stress_pass",
]
