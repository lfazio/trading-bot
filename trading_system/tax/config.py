"""``TaxConfig`` — frozen tax-engine parameters.

Sourced from ``config/tax.yaml``. Once loaded the value is passed
explicitly to engine functions; engine modules never reach back to
configuration globals.

REQ refs: REQ_F_TAX_001..003, REQ_F_TAX_006, REQ_SDD_CFG_001,
REQ_SDD_CFG_002, REQ_SDS_INT_004 (frozen Config), REQ_SDD_API_004.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

JANUARY = 1
DECEMBER = 12


@dataclass(frozen=True, slots=True)
class TaxConfig:
    """Tax-engine parameters.

    Defaults match France CTO / PFU: 30 % flat tax (REQ_SDD_CFG_001),
    5x trade-gate multiplier (REQ_SDD_CFG_002), calendar-year
    accounting (``fiscal_year_end_month = 12``).
    """

    rate: Decimal
    gate_multiplier: int
    fiscal_year_end_month: int = DECEMBER

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.rate <= Decimal(1)):
            raise ValueError(f"TaxConfig.rate must lie in [0, 1], got {self.rate}")
        if self.gate_multiplier <= 0:
            raise ValueError(f"TaxConfig.gate_multiplier must be > 0, got {self.gate_multiplier}")
        if not (JANUARY <= self.fiscal_year_end_month <= DECEMBER):
            raise ValueError(
                f"TaxConfig.fiscal_year_end_month must lie in [{JANUARY}, {DECEMBER}], "
                f"got {self.fiscal_year_end_month}"
            )

    @classmethod
    def default(cls) -> TaxConfig:
        """Convenience factory matching ``config/tax.yaml`` defaults."""
        return cls(
            rate=Decimal("0.30"),
            gate_multiplier=5,
            fiscal_year_end_month=DECEMBER,
        )
