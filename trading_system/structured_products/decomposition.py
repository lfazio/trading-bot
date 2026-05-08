"""``Decomposition`` — the four-field signature every structured
product must produce to be admissible (REQ_F_STP_002, REQ_SDD_ALG_012).

If a payoff cannot be decomposed into these four numbers, the
product is rejected before any allocation logic runs
(REQ_SDS_MOD_008).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Decomposition:
    """Risk decomposition of a structured product.

    - ``equity_equiv`` — equity-equivalent exposure as a fraction of
      the product's notional (e.g., 0.85 = 85% of notional behaves
      like a stock position).
    - ``hidden_leverage`` — implicit leverage embedded in the payoff
      beyond the equity equivalent (e.g., a leveraged certificate
      with 2x notional exposure has hidden_leverage = 1.0 on top of
      equity_equiv).
    - ``worst_case_loss`` — fraction of notional that can be lost in
      the worst documented scenario (e.g., 0.40 = 40% loss).
    - ``break_even_prob`` — probability that the product breaks even
      under the issuer's stated reference distribution; in [0, 1].
    """

    equity_equiv: Decimal
    hidden_leverage: Decimal
    worst_case_loss: Decimal
    break_even_prob: Decimal

    def __post_init__(self) -> None:
        if self.equity_equiv < 0:
            raise ValueError(f"Decomposition.equity_equiv must be >= 0, got {self.equity_equiv}")
        if self.hidden_leverage < 0:
            raise ValueError(
                f"Decomposition.hidden_leverage must be >= 0, got {self.hidden_leverage}"
            )
        if not (Decimal(0) <= self.worst_case_loss <= Decimal(1)):
            raise ValueError(
                f"Decomposition.worst_case_loss must lie in [0, 1], got {self.worst_case_loss}"
            )
        if not (Decimal(0) <= self.break_even_prob <= Decimal(1)):
            raise ValueError(
                f"Decomposition.break_even_prob must lie in [0, 1], got {self.break_even_prob}"
            )
