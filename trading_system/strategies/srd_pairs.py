"""CR-030 — SRDPairsStrategy reference strategy.

Demonstrates the canonical SRD use case: long the outperforming
stock of a sector pair + short the underperformer, both routed
through the SRD deferred-settlement venue. Captures the
sector-relative-strength alpha while staying broadly
delta-neutral (the long and short legs offset most index
exposure).

**Status:** reference exemplar only. Not promoted to validated
status — operators validate via the CR-002 hypothesis flow + the
meta-optimization loop. Lives in ``trading_system/strategies/``
so the dashboard can offer it as a wizard preset, but
``strategy_lab/`` never imports it.

REQ refs:
- REQ_F_SRD_005 (backtest integration via OrderType.SRD_LONG /
  OrderType.SRD_SHORT proposals).
- REQ_C_BHV_001 (prefer stocks over turbos when edge is strong —
  SRD is "stock with leverage", complementary).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.models.identifiers import StrategyId
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Money
from trading_system.models.trading import OrderType, Side, StopLoss
from trading_system.strategies.state import MarketState


DEFAULT_SRD_PAIRS_ID = StrategyId("srd-pairs-reference")


@dataclass(frozen=True, slots=True)
class SRDPairsStrategyConfig:
    """Tunable knobs for the reference long/short SRD pair.

    Defaults pick a conservative size (1% per leg, 2% combined
    sector exposure) so the operator's first SRD backtest runs
    inside the CR-006 Phase-1 risk band without further tuning.
    """

    leg_size_pct_of_capital: Decimal = Decimal("0.01")
    stop_loss_pct: Decimal = Decimal("0.05")
    min_ranking_gap: Decimal = Decimal("0.10")
    expected_net_profit_pct: Decimal = Decimal("0.03")


class SRDPairsStrategy:
    """Reference SRD long/short pair signal generator.

    On every ``evaluate(state)`` call:

    1. Pull the screener ranking sorted by descending score.
    2. Pick the TOP-RANKED stock the portfolio doesn't already
       hold (long leg).
    3. Pick the BOTTOM-RANKED stock from the same sector
       (short leg). When no sector match exists, the strategy
       skips the cycle — pairs are sector-relative.
    4. Emit two ``TradeProposal``s with
       ``order_type=OrderType.SRD_LONG`` / ``SRD_SHORT``
       respectively. The runtime materialises the pair into
       SRD orders + opens both legs in the portfolio's
       ``srd_positions`` ledger atomically (no fill = no
       leg = no half-hedged position).
    """

    id: StrategyId

    def __init__(
        self,
        cfg: SRDPairsStrategyConfig | None = None,
        *,
        strategy_id: StrategyId = DEFAULT_SRD_PAIRS_ID,
    ) -> None:
        self.id = strategy_id
        self._cfg = cfg or SRDPairsStrategyConfig()

    def evaluate(self, state: MarketState) -> list[TradeProposal]:
        equity = state.portfolio.equity()
        if equity.amount <= 0:
            return []
        ranking = state.screener_ranking
        if len(ranking) < 2:
            return []
        # Sort descending by score; defensive copy.
        ordered = sorted(ranking, key=lambda r: r.score, reverse=True)
        top = ordered[0]
        # Find the lowest-ranked stock that shares the top's sector
        # AND that the portfolio doesn't already hold.
        bottom = None
        for candidate in reversed(ordered):
            if candidate.stock.id == top.stock.id:
                continue
            if candidate.stock.sector != top.stock.sector:
                continue
            if state.portfolio.holds(candidate.stock.id):
                continue
            bottom = candidate
            break
        if bottom is None:
            return []
        # Spread filter — skip when winner and loser are too close.
        if top.score - bottom.score < self._cfg.min_ranking_gap:
            return []
        # Skip when the portfolio already holds the long leg.
        if state.portfolio.holds(top.stock.id):
            return []
        long_proposal = self._make_proposal(
            equity=equity,
            stock=top.stock,
            side=Side.BUY,
            order_type=OrderType.SRD_LONG,
        )
        short_proposal = self._make_proposal(
            equity=equity,
            stock=bottom.stock,
            side=Side.SELL,
            order_type=OrderType.SRD_SHORT,
        )
        return [long_proposal, short_proposal]

    def _make_proposal(
        self,
        *,
        equity: Money,
        stock,  # type: ignore[no-untyped-def]
        side: Side,
        order_type: OrderType,
    ) -> TradeProposal:
        notional = Money(
            equity.amount * self._cfg.leg_size_pct_of_capital,
            equity.currency,
        )
        stop_price = (
            Decimal(1) + self._cfg.stop_loss_pct
            if side is Side.SELL
            else Decimal(1) - self._cfg.stop_loss_pct
        )
        # Reference price is the screener's score-baked price (we
        # don't have a bar series here; the runtime fills against
        # the live tick price). Use a placeholder stop at 1.0 ± pct
        # of the screener ranking's score baseline.
        stop = StopLoss(price=Decimal(1) * stop_price)
        # Stop must be positive; clamp defensively for the SHORT
        # leg where the multiplier > 1 (stop > entry is correct
        # for shorts) and the LONG leg where it < 1 (stop < entry).
        if stop.price <= 0:
            stop = StopLoss(price=Decimal("0.01"))
        expected_net_profit = Money(
            notional.amount * self._cfg.expected_net_profit_pct,
            equity.currency,
        )
        return TradeProposal(
            instrument=stock,
            side=side,
            size_pct_of_capital=self._cfg.leg_size_pct_of_capital,
            expected_net_profit=expected_net_profit,
            expected_fees=Money(Decimal(0), equity.currency),
            stop_loss=stop,
            source_strategy=self.id,
            order_type=order_type,
        )
