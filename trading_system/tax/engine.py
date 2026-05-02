"""Pure tax math: net gain, net dividend, and the trade-aware gate.

REQ refs:
- REQ_F_TAX_001 — ``net_gain(gross) = gross x (1 - rate)``.
- REQ_F_TAX_002 — ``net_dividend(gross) = gross x (1 - rate)``.
- REQ_F_TAX_003 — trade is valid only if
  ``expected_net_profit > gate_multiplier x total_fees`` (after tax).
- REQ_F_TAX_004 — engine returns post-tax amounts only.
- REQ_SDD_ALG_001 — round HALF_UP to 2 decimal places.
- REQ_SDS_MOD_003 — pure functions; rate sourced from ``TaxConfig``,
  never a module global.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from trading_system.models.money import Money
from trading_system.tax.config import TaxConfig

_CENT = Decimal("0.01")


def _round_half_up_cents(amount: Decimal) -> Decimal:
    """Round ``amount`` to 2 decimal places using ROUND_HALF_UP
    (REQ_SDD_ALG_001)."""
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def net_gain(cfg: TaxConfig, gross: Money) -> Money:
    """Apply tax on a realized capital gain (REQ_F_TAX_001).

    Negative ``gross`` (a loss) is returned unchanged — losses are not
    "taxed positively" in the CTO regime; they are credits the harvester
    uses to offset gains within the fiscal year (REQ_F_TAX_006).
    """
    if gross.amount < 0:
        return Money(_round_half_up_cents(gross.amount), gross.currency)
    net = gross.amount * (Decimal(1) - cfg.rate)
    return Money(_round_half_up_cents(net), gross.currency)


def net_dividend(cfg: TaxConfig, gross: Money) -> Money:
    """Apply tax on a dividend (REQ_F_TAX_002). Dividends are always
    non-negative; negative input is a programmer error and panics."""
    assert gross.amount >= 0, f"net_dividend called with negative gross: {gross.amount}"
    net = gross.amount * (Decimal(1) - cfg.rate)
    return Money(_round_half_up_cents(net), gross.currency)


def trade_passes_gate(cfg: TaxConfig, expected_net_profit: Money, total_fees: Money) -> bool:
    """Tax-aware trade gate (REQ_F_TAX_003).

    Inputs are AFTER tax. The check is strict greater-than:
    boundary cases (``net == k x fees``) fail by design — marginal
    trades are auto-rejected (REQ_C_BHV_003).
    """
    assert expected_net_profit.currency == total_fees.currency, (
        f"trade_passes_gate cross-currency: {expected_net_profit.currency} vs {total_fees.currency}"
    )
    return expected_net_profit.amount > total_fees.amount * cfg.gate_multiplier
