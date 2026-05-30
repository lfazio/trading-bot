"""CR-025 — broker factory for the `trading-bot live-preflight` CLI.

Sits under ``webapp/runtimes/`` so it's covered by the documented
structural carve-out (REQ_SDD_FAS_001 amendment from CR-019 step 1):
modules here MAY reach into ``execution.*`` / ``data.*`` /
``portfolio.*`` / etc., where the router + view + plain-webapp
modules cannot.

Maps the configured ``broker.adapter`` selector to a concrete
adapter the preflight gates can consult.

REQ refs: REQ_F_PAP_014, REQ_F_BRK_003, REQ_SDD_PAP_005.
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.data.mock import MockMarketDataProvider
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.paper import PaperBrokerAdapter
from trading_system.execution.slippage import ZeroSlippageModel
from trading_system.models.money import Money


def build_broker_for_preflight(system_config) -> object:
    """Operator-facing factory for the preflight CLI (REQ_F_PAP_014).

    - ``"paper"`` ⇒ ``PaperBrokerAdapter`` wired against the
      configured ``MarketDataProvider``.
    - any other selector ⇒ a small ``_NotConfiguredBroker`` stub
      that fails the ``broker_authenticate`` gate cleanly so the
      JSON artefact carries the documented failure reason. Concrete
      live-broker adapters land via their own SRS amendments + this
      factory grows to cover them.
    """
    selector = system_config.broker_adapter.strip().lower()
    if selector == "paper":
        return PaperBrokerAdapter(
            starting_cash=system_config.starting_capital,
            market_data=MockMarketDataProvider(seed=system_config.seed),
            fee_model=FlatFeeModel(
                commission=Money(
                    Decimal("0"), system_config.starting_capital.currency
                ),
                spread_bps=Decimal("0"),
            ),
            slippage_model=ZeroSlippageModel(),
            seed=system_config.seed,
        )

    class _NotConfiguredBroker:
        def account_state(self):
            raise RuntimeError(
                f"broker.adapter is {selector!r}; no concrete live broker "
                "configured (REQ_F_BRK_003)."
            )

    return _NotConfiguredBroker()
