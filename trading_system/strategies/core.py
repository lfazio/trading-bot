"""``CoreStrategy`` — long-term holding, dividend compounding, low turnover.

The core strategy targets the phase's ``AllocationBucket.STOCK``
allocation and proposes BUYs from the top of the screener ranking
when current exposure falls behind the target by at least
``rebalance_band``. Existing holdings are skipped (low turnover —
REQ_C_BHV_002, REQ_F_STR_001). The strategy never proposes SELLs;
liquidation decisions are the risk engine's job (stop-loss) or the
operator's.

REQ refs:
- REQ_F_STR_001 — long-term / dividend / low-turnover behavior.
- REQ_C_BHV_001 — prefer stocks over turbos (the core strategy is
  the stock-side of that preference).
- REQ_C_BHV_002 — avoid overtrading; the rebalance_band gate
  enforces this.
- REQ_SDS_MOD_006 / REQ_SDD_API_001 — read-only over state.
- REQ_SDD_API_005 — stable strategy id.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.execution.fees import FeeModel
from trading_system.models.identifiers import StrategyId
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Money
from trading_system.models.phase import AllocationBucket
from trading_system.models.trading import Side, StopLoss
from trading_system.result import Err, Ok
from trading_system.strategies._estimates import (
    estimate_fees,
    estimate_net_profit,
)
from trading_system.strategies.state import MarketState
from trading_system.tax.config import TaxConfig

DEFAULT_STRATEGY_ID = StrategyId("core_v1")


@dataclass(frozen=True, slots=True)
class CoreStrategyConfig:
    """Tunables for ``CoreStrategy``.

    - ``rebalance_band``: minimum exposure gap (target - current)
      that justifies a tick of trading. Below this, no proposals.
      Anchors REQ_C_BHV_002.
    - ``tick_budget_pct``: cap on the total new allocation per call;
      prevents one rebalance from filling the entire gap and burning
      fees on a single tick.
    - ``max_position_pct``: per-position size cap.
    - ``stop_loss_pct``: stop-loss placed at ``(1 - pct)`` of entry.
    - ``expected_return_pct``: assumed annualized total return for
      the after-tax profit estimate (used by the tax gate). A
      conservative default avoids gating-out reasonable trades.
    """

    rebalance_band: Decimal = Decimal("0.02")
    tick_budget_pct: Decimal = Decimal("0.10")
    max_position_pct: Decimal = Decimal("0.10")
    stop_loss_pct: Decimal = Decimal("0.20")
    expected_return_pct: Decimal = Decimal("0.06")  # 6% annual

    def __post_init__(self) -> None:
        for label, v in (
            ("rebalance_band", self.rebalance_band),
            ("tick_budget_pct", self.tick_budget_pct),
            ("max_position_pct", self.max_position_pct),
            ("stop_loss_pct", self.stop_loss_pct),
            ("expected_return_pct", self.expected_return_pct),
        ):
            if v < 0:
                raise ValueError(f"CoreStrategyConfig.{label} must be >= 0, got {v}")
        if not (Decimal(0) < self.stop_loss_pct < Decimal(1)):
            raise ValueError(
                f"CoreStrategyConfig.stop_loss_pct must lie in (0, 1), got {self.stop_loss_pct}"
            )
        if self.tick_budget_pct > Decimal(1):
            raise ValueError(
                f"CoreStrategyConfig.tick_budget_pct must be <= 1, got {self.tick_budget_pct}"
            )
        if self.max_position_pct > Decimal(1):
            raise ValueError(
                f"CoreStrategyConfig.max_position_pct must be <= 1, got {self.max_position_pct}"
            )


class CoreStrategy:
    """Long-term core-equity rebalancer (REQ_F_STR_001)."""

    id: StrategyId

    def __init__(
        self,
        cfg: CoreStrategyConfig,
        fee_model: FeeModel,
        tax_cfg: TaxConfig,
        *,
        strategy_id: StrategyId = DEFAULT_STRATEGY_ID,
    ) -> None:
        self.id = strategy_id
        self._cfg = cfg
        self._fee_model = fee_model
        self._tax = tax_cfg

    def evaluate(self, state: MarketState) -> list[TradeProposal]:
        target = state.constraints.allocation_targets.get(AllocationBucket.STOCK, Decimal(0))
        current = state.portfolio.exposure_pct(AllocationBucket.STOCK)
        gap = target - current
        if gap < self._cfg.rebalance_band:
            return []

        equity = state.portfolio.equity()
        if equity.amount <= 0:
            return []

        proposals: list[TradeProposal] = []
        budget = min(gap, self._cfg.tick_budget_pct)
        for ranked in state.screener_ranking:
            if budget <= 0:
                break
            stock = ranked.stock
            if state.portfolio.holds(stock.id):
                continue
            size = min(budget, self._cfg.max_position_pct)
            if size <= 0:
                continue

            entry_price = self._latest_price(state, stock)
            if entry_price is None:
                continue

            notional = Money(equity.amount * size, equity.currency)
            quantity = notional.amount / entry_price
            if quantity <= 0:
                continue

            stop = StopLoss(price=entry_price * (Decimal(1) - self._cfg.stop_loss_pct))
            fees = estimate_fees(
                self._fee_model,
                instrument=stock,
                side=Side.BUY,
                quantity=quantity,
                fill_price=entry_price,
                stop_loss=stop,
                source_strategy=self.id,
                at=state.at,
            )
            net_profit = estimate_net_profit(
                self._tax,
                notional=notional,
                expected_return_pct=self._cfg.expected_return_pct,
            )
            proposals.append(
                TradeProposal(
                    instrument=stock,
                    side=Side.BUY,
                    size_pct_of_capital=size,
                    expected_net_profit=net_profit,
                    expected_fees=fees,
                    stop_loss=stop,
                    source_strategy=self.id,
                )
            )
            budget -= size
        return proposals

    def _latest_price(self, state: MarketState, stock: object) -> Decimal | None:
        match state.market.latest(stock):  # type: ignore[arg-type]
            case Ok(bar):
                return bar.close
            case Err(_):
                return None
