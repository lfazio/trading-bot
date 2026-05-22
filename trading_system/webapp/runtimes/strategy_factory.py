"""Strategy factory for the onboarding wizard.

Lives under ``webapp/runtimes/`` (not under ``webapp/routers/views/``)
because the structural-audit allow-list for the views layer
doesn't permit ``trading_system.strategies.*`` reach. Runtimes
DO get to import strategies (it's in the carve-out documented in
``tests/webapp/test_structural.py``).

REQ refs:
- REQ_F_WEB2_001 — wizard's finish handler dispatches by strategy
  name to a concrete ``Strategy`` instance.
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.execution.fees import FlatFeeModel
from trading_system.models.identifiers import StrategyId
from trading_system.models.money import Currency, Money
from trading_system.strategies.core import CoreStrategy, CoreStrategyConfig
from trading_system.strategies.protocol import Strategy
from trading_system.strategies.tactical import (
    TacticalStrategy,
    TacticalStrategyConfig,
)
from trading_system.tax.config import TaxConfig


def build_strategy(
    name: str,
    *,
    strategy_id: StrategyId,
    fee_model: FlatFeeModel | None = None,
    tax_cfg: TaxConfig | None = None,
) -> Strategy | None:
    """Construct a concrete ``Strategy`` by name. Returns ``None``
    for an unknown name (the wizard surfaces the error)."""
    fee = fee_model or FlatFeeModel(
        commission=Money(Decimal("0"), Currency.EUR),
        spread_bps=Decimal("0"),
    )
    tax = tax_cfg or TaxConfig.default()
    if name == "CoreStrategy":
        return CoreStrategy(
            cfg=CoreStrategyConfig(),
            fee_model=fee,
            tax_cfg=tax,
            strategy_id=strategy_id,
        )
    if name == "TacticalStrategy":
        return TacticalStrategy(
            cfg=TacticalStrategyConfig(),
            fee_model=fee,
            tax_cfg=tax,
            strategy_id=strategy_id,
        )
    return None
