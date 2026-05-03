"""Tests for ``trading_system.strategies.tactical``.

Verifies REQ_F_STR_002 (trend / breakout / pullback signals),
REQ_F_CAP_013 (size from risk_per_trade_band lo), REQ_F_CAP_014
(stop-loss mandatory), REQ_SDD_API_005 (stable id).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.data.types import Bar
from trading_system.models.identifiers import StrategyId
from trading_system.models.trading import Position, StopLoss
from trading_system.strategies.tactical import (
    TacticalStrategy,
    TacticalStrategyConfig,
    detect_breakout,
    detect_pullback,
    detect_trend,
    moving_average,
)
from trading_system.tax.config import TaxConfig

from .conftest import (
    StubMarketProvider,
    StubPortfolioView,
    make_fee_model,
    make_scored_stock,
    make_state,
    make_stock,
    synthetic_bars,
)


def descending_bars(*, base: str = "100", delta: str = "0.5", count: int = 60) -> list[Bar]:
    """Linearly decreasing closes ending on the default state.at —
    guarantees a downtrend within the strategy's lookback window."""
    bars: list[Bar] = []
    price = Decimal(base)
    step = Decimal(delta)
    end_at = datetime(2026, 5, 1)
    start = end_at - timedelta(days=count - 1)
    for i in range(count):
        c = price - step * Decimal(i)
        bars.append(
            Bar(
                at=start + timedelta(days=i),
                open=c,
                high=c,
                low=c,
                close=c,
                volume=Decimal(1000),
            )
        )
    return bars


# ---------------------------------------------------------------------------
# Pure signal helpers
# ---------------------------------------------------------------------------


class TestMovingAverage:
    def test_basic(self) -> None:
        closes = [Decimal(i) for i in range(1, 6)]  # 1..5
        assert moving_average(closes, 5) == Decimal(3)

    def test_returns_none_when_not_enough(self) -> None:
        assert moving_average([Decimal(1), Decimal(2)], 5) is None

    def test_invalid_window_returns_none(self) -> None:
        assert moving_average([Decimal(1)], 0) is None


class TestDetectTrend:
    def test_uptrend(self) -> None:
        closes = [Decimal(i) for i in range(1, 100)]  # increasing
        assert detect_trend(closes, 20, 50) is True

    def test_downtrend(self) -> None:
        closes = [Decimal(100 - i) for i in range(99)]  # decreasing
        assert detect_trend(closes, 20, 50) is False

    def test_insufficient_data(self) -> None:
        assert detect_trend([Decimal(1)] * 10, 20, 50) is False


class TestDetectBreakout:
    def test_breakout_fires(self) -> None:
        # 21 bars at 100, then 110.
        closes = [Decimal(100)] * 20 + [Decimal(110)]
        assert detect_breakout(closes, 20) is True

    def test_no_breakout_when_below_high(self) -> None:
        closes = [Decimal(100)] * 20 + [Decimal(99)]
        assert detect_breakout(closes, 20) is False

    def test_insufficient_data(self) -> None:
        assert detect_breakout([Decimal(1)] * 10, 20) is False


class TestDetectPullback:
    def test_pullback_in_uptrend(self) -> None:
        # Build an uptrend, then drop to the long MA so close <= long_ma.
        # Trend is 1..99, then add a few bars at price equal to long MA.
        rising = [Decimal(i) for i in range(1, 100)]
        long_ma = sum(rising[-50:]) / Decimal(50)
        closes = [*rising, long_ma]
        assert detect_pullback(closes, 20, 50) is True

    def test_no_pullback_in_downtrend(self) -> None:
        closes = [Decimal(100 - i) for i in range(99)]
        assert detect_pullback(closes, 20, 50) is False


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestTacticalConfig:
    def test_defaults_valid(self) -> None:
        cfg = TacticalStrategyConfig()
        assert cfg.short_ma_window == 20
        assert cfg.long_ma_window == 50

    def test_short_must_be_below_long(self) -> None:
        with pytest.raises(ValueError, match="short_ma_window must be"):
            TacticalStrategyConfig(short_ma_window=50, long_ma_window=20)

    @pytest.mark.parametrize("window", [0, -1])
    def test_non_positive_windows_rejected(self, window: int) -> None:
        with pytest.raises(ValueError):
            TacticalStrategyConfig(short_ma_window=window)

    @pytest.mark.parametrize("v", [Decimal(0), Decimal(1)])
    def test_invalid_stop_loss_rejected(self, v: Decimal) -> None:
        with pytest.raises(ValueError, match="stop_loss_pct"):
            TacticalStrategyConfig(stop_loss_pct=v)


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


class TestTacticalEvaluate:
    def _strategy(self) -> TacticalStrategy:
        return TacticalStrategy(
            TacticalStrategyConfig(),
            make_fee_model(),
            TaxConfig.default(),
        )

    def test_id_is_stable(self) -> None:
        assert self._strategy().id == StrategyId("tactical_v1")

    def test_emits_proposal_on_uptrend(self) -> None:
        stock = make_stock("UPT")
        market = StubMarketProvider()
        bars = synthetic_bars(count=60)
        market.bars_map[stock.id] = bars
        state = make_state(
            portfolio=StubPortfolioView(),
            screener_ranking=(make_scored_stock(stock=stock),),
            market=market,
        )
        proposals = self._strategy().evaluate(state)
        assert len(proposals) == 1
        p = proposals[0]
        # Size from risk-per-trade band lo (default phase: 0.01).
        assert p.size_pct_of_capital == Decimal("0.01")
        # Stop-loss is set below the entry close.
        assert p.stop_loss.price < bars[-1].close

    def test_no_proposal_on_downtrend(self) -> None:
        stock = make_stock("DWN")
        market = StubMarketProvider()
        market.bars_map[stock.id] = descending_bars(count=60)
        state = make_state(
            portfolio=StubPortfolioView(),
            screener_ranking=(make_scored_stock(stock=stock),),
            market=market,
        )
        assert self._strategy().evaluate(state) == []

    def test_skips_held_stocks(self) -> None:
        held = make_stock("HLD")
        market = StubMarketProvider()
        market.bars_map[held.id] = synthetic_bars(count=60)
        position = Position(
            instrument=held,
            quantity=Decimal(5),
            avg_price=Decimal("100"),
            opened_at=datetime(2026, 1, 1),
            stop_loss=StopLoss(price=Decimal("80")),
        )
        state = make_state(
            portfolio=StubPortfolioView(positions={held.id: position}),
            screener_ranking=(make_scored_stock(stock=held),),
            market=market,
        )
        assert self._strategy().evaluate(state) == []

    def test_skips_when_bars_unavailable(self) -> None:
        stock = make_stock("ABC")
        # No bars_map entry — provider returns Err.
        state = make_state(
            screener_ranking=(make_scored_stock(stock=stock),),
        )
        assert self._strategy().evaluate(state) == []

    def test_zero_equity_short_circuits(self) -> None:
        stock = make_stock("ABC")
        market = StubMarketProvider()
        market.bars_map[stock.id] = synthetic_bars(count=60)
        state = make_state(
            portfolio=StubPortfolioView(equity_amount="0", cash_amount="0"),
            screener_ranking=(make_scored_stock(stock=stock),),
            market=market,
        )
        assert self._strategy().evaluate(state) == []

    def test_top_n_caps_universe(self) -> None:
        cfg = TacticalStrategyConfig(top_n_candidates=2)
        strat = TacticalStrategy(cfg, make_fee_model(), TaxConfig.default())
        market = StubMarketProvider()
        rankings = []
        for sym in ("AAA", "BBB", "CCC", "DDD"):
            stock = make_stock(sym, isin_suffix=sym)
            market.bars_map[stock.id] = synthetic_bars(count=60)
            rankings.append(make_scored_stock(stock=stock))
        state = make_state(
            portfolio=StubPortfolioView(),
            screener_ranking=tuple(rankings),
            market=market,
        )
        proposals = strat.evaluate(state)
        assert len(proposals) == 2  # only top 2 considered

    def test_short_history_skipped(self) -> None:
        # Fewer than long_ma_window + 1 bars -> skipped.
        stock = make_stock("ABC")
        market = StubMarketProvider()
        market.bars_map[stock.id] = synthetic_bars(count=10)
        state = make_state(
            screener_ranking=(make_scored_stock(stock=stock),),
            market=market,
        )
        assert self._strategy().evaluate(state) == []
