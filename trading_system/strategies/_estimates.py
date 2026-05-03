"""Shared estimators used by concrete strategies.

These compute the *expected* values a ``TradeProposal`` carries
(``expected_net_profit``, ``expected_fees``). The actual realized
values come from the broker at fill time; strategies provide best
estimates so the tax-aware gate (``REQ_F_TAX_003``) can run.

REQ refs: REQ_F_TAX_003, REQ_SDD_DAT_005 (estimates live on
TradeProposal only; actuals on Trade).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from trading_system.execution.fees import FeeModel
from trading_system.models.identifiers import OrderId, StrategyId
from trading_system.models.instrument import Instrument
from trading_system.models.money import Money
from trading_system.models.trading import Order, OrderType, Side, StopLoss
from trading_system.tax.config import TaxConfig
from trading_system.tax.engine import net_gain


def estimate_fees(  # noqa: PLR0913 - mirrors the Order constructor surface
    fee_model: FeeModel,
    *,
    instrument: Instrument,
    side: Side,
    quantity: Decimal,
    fill_price: Decimal,
    stop_loss: StopLoss,
    source_strategy: StrategyId,
    at: datetime,
    draft_id: str = "draft",
) -> Money:
    """Build a draft ``Order`` and ask the fee model what it would
    cost to fill at ``fill_price``."""
    draft = Order(
        id=OrderId(draft_id),
        instrument=instrument,
        side=side,
        quantity=quantity,
        type=OrderType.MARKET,
        stop_loss=stop_loss,
        created_at=at,
        source_strategy=source_strategy,
    )
    return fee_model.fees(draft, fill_price)


def estimate_net_profit(
    tax_cfg: TaxConfig,
    *,
    notional: Money,
    expected_return_pct: Decimal,
) -> Money:
    """Compute the after-tax expected profit on ``notional`` given
    the strategy's assumed gross return rate."""
    gross = Money(notional.amount * expected_return_pct, notional.currency)
    return net_gain(tax_cfg, gross)
