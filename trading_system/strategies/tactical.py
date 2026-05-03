"""``TacticalStrategy`` — trend / breakout / pullback signal generator.

For each top-N candidate from the screener ranking, ``evaluate``
computes three signals on the recent daily bars:

- **Trend**:      short MA strictly above long MA (e.g. 20d > 50d).
- **Breakout**:   today's close strictly above the prior N-day high.
- **Pullback**:   in an uptrend, today's close at-or-below the long
                  MA — a "buy the dip" entry.

A candidate triggers a BUY proposal when *any* signal fires; the
proposal carries a tight stop-loss (``stop_loss_pct`` below entry)
and the per-trade size is taken from the lower bound of the phase's
``risk_per_trade_band`` (REQ_F_CAP_013) so tactical sizing is
per-phase consistent with the SRS without re-deriving it here.

REQ refs:
- REQ_F_STR_002 — trend / breakout / pullback signals.
- REQ_F_CAP_013 — risk-per-trade band drives the size.
- REQ_F_CAP_014 / REQ_SDD_DAT_001 — stop-loss is mandatory.
- REQ_SDS_MOD_006 / REQ_SDD_API_001 — read-only over state.
- REQ_SDD_API_005 — stable strategy id.
- REQ_SDS_FLO_002 — phase constraints distributed by the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from trading_system.data.types import Bar, Timeframe
from trading_system.execution.fees import FeeModel
from trading_system.models.identifiers import StrategyId
from trading_system.models.instrument import Stock
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Money
from trading_system.models.trading import Side, StopLoss
from trading_system.result import Err, Ok
from trading_system.strategies._estimates import (
    estimate_fees,
    estimate_net_profit,
)
from trading_system.strategies.state import MarketState
from trading_system.tax.config import TaxConfig

DEFAULT_STRATEGY_ID = StrategyId("tactical_v1")


@dataclass(frozen=True, slots=True)
class TacticalSignal:
    """Diagnostic record describing why a tactical proposal fired.
    Strategies do not have to expose this — the field exists so tests
    and downstream analytics can audit signal provenance."""

    kind: str  # "trend" | "breakout" | "pullback"
    short_ma: Decimal
    long_ma: Decimal
    breakout_high: Decimal
    last_close: Decimal


@dataclass(frozen=True, slots=True)
class TacticalStrategyConfig:
    """Tunables for ``TacticalStrategy``."""

    short_ma_window: int = 20
    long_ma_window: int = 50
    breakout_window: int = 20
    top_n_candidates: int = 5
    stop_loss_pct: Decimal = Decimal("0.05")
    expected_return_pct: Decimal = Decimal("0.04")  # per-trade target

    def __post_init__(self) -> None:
        for label, v in (
            ("short_ma_window", self.short_ma_window),
            ("long_ma_window", self.long_ma_window),
            ("breakout_window", self.breakout_window),
            ("top_n_candidates", self.top_n_candidates),
        ):
            if v <= 0:
                raise ValueError(f"TacticalStrategyConfig.{label} must be > 0, got {v}")
        if self.short_ma_window >= self.long_ma_window:
            raise ValueError(
                "TacticalStrategyConfig.short_ma_window must be < long_ma_window, "
                f"got ({self.short_ma_window}, {self.long_ma_window})"
            )
        if not (Decimal(0) < self.stop_loss_pct < Decimal(1)):
            raise ValueError(
                f"TacticalStrategyConfig.stop_loss_pct must lie in (0, 1), got {self.stop_loss_pct}"
            )
        if self.expected_return_pct < 0:
            raise ValueError(
                f"TacticalStrategyConfig.expected_return_pct must be >= 0, "
                f"got {self.expected_return_pct}"
            )


# ---------------------------------------------------------------------------
# Pure signal helpers — testable independently
# ---------------------------------------------------------------------------


def moving_average(closes: list[Decimal], window: int) -> Decimal | None:
    """Simple moving average of the last ``window`` closes; ``None``
    when fewer than ``window`` bars are available."""
    if window <= 0 or len(closes) < window:
        return None
    tail = closes[-window:]
    return sum(tail, start=Decimal(0)) / Decimal(window)


def detect_trend(closes: list[Decimal], short: int, long: int) -> bool:
    short_ma = moving_average(closes, short)
    long_ma = moving_average(closes, long)
    if short_ma is None or long_ma is None:
        return False
    return short_ma > long_ma


def detect_breakout(closes: list[Decimal], window: int) -> bool:
    if window <= 0 or len(closes) < window + 1:
        return False
    last = closes[-1]
    prior_high = max(closes[-(window + 1) : -1])
    return last > prior_high


def detect_pullback(closes: list[Decimal], short: int, long: int) -> bool:
    """In a confirmed trend, fire when today's close is at-or-below
    the long MA — entering on the dip."""
    if not detect_trend(closes, short, long):
        return False
    long_ma = moving_average(closes, long)
    if long_ma is None:
        return False
    return closes[-1] <= long_ma


# ---------------------------------------------------------------------------
# TacticalStrategy
# ---------------------------------------------------------------------------


class TacticalStrategy:
    """Trend / breakout / pullback signal generator (REQ_F_STR_002)."""

    id: StrategyId

    def __init__(
        self,
        cfg: TacticalStrategyConfig,
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
        equity = state.portfolio.equity()
        if equity.amount <= 0:
            return []
        size_lo, _ = state.constraints.risk_per_trade_band

        proposals: list[TradeProposal] = []
        for ranked in state.screener_ranking[: self._cfg.top_n_candidates]:
            stock = ranked.stock
            if state.portfolio.holds(stock.id):
                continue
            bars = self._fetch_bars(state, stock)
            if bars is None:
                continue
            signal = self._signal(bars)
            if signal is None:
                continue

            entry_price = bars[-1].close
            notional = Money(equity.amount * size_lo, equity.currency)
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
                    size_pct_of_capital=size_lo,
                    expected_net_profit=net_profit,
                    expected_fees=fees,
                    stop_loss=stop,
                    source_strategy=self.id,
                )
            )
        return proposals

    # --- internals ----------------------------------------------------

    def _fetch_bars(self, state: MarketState, stock: Stock) -> list[Bar] | None:
        # Need at least long_ma_window + 1 daily bars for breakout to
        # have a prior high. Fetch a generous lookback to make sure
        # the synthetic mock provider produces enough data.
        lookback = max(self._cfg.long_ma_window, self._cfg.breakout_window) + 5
        start = state.at - timedelta(days=lookback)
        result = state.market.bars(stock, Timeframe.D1, start, state.at)
        match result:
            case Err(_):
                return None
            case Ok(bars):
                if len(bars) < self._cfg.long_ma_window + 1:
                    return None
                return bars

    def _signal(self, bars: list[Bar]) -> TacticalSignal | None:
        closes = [b.close for b in bars]
        kind: str | None = None
        if detect_trend(closes, self._cfg.short_ma_window, self._cfg.long_ma_window):
            kind = "trend"
        if detect_breakout(closes, self._cfg.breakout_window):
            kind = "breakout"  # breakout overrides trend label
        if detect_pullback(closes, self._cfg.short_ma_window, self._cfg.long_ma_window):
            kind = "pullback"
        if kind is None:
            return None
        short_ma = moving_average(closes, self._cfg.short_ma_window) or Decimal(0)
        long_ma = moving_average(closes, self._cfg.long_ma_window) or Decimal(0)
        breakout_high = max(closes[-(self._cfg.breakout_window + 1) : -1])
        return TacticalSignal(
            kind=kind,
            short_ma=short_ma,
            long_ma=long_ma,
            breakout_high=breakout_high,
            last_close=closes[-1],
        )
