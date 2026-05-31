"""CR-030 — SRDPairsStrategy reference tests.

Reference / exemplar strategy. Operators validate via the
CR-002 hypothesis flow; these tests pin the signal logic so a
refactor doesn't silently change the emitted proposals.

REQ refs:
- REQ_F_SRD_002 (TradeProposal.order_type = SRD_LONG / SRD_SHORT).
- REQ_F_SRD_005 (backtest integration through the SRD scheduler
  consumes these proposals).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import InstrumentId, StrategyId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.phase import (
    AllocationBucket,
    MarketRegime,
    PhaseConstraints,
)
from trading_system.models.trading import OrderType, Side
from trading_system.portfolio.portfolio import Portfolio
from trading_system.screener.engine import ScoreBreakdown, ScoredStock
from trading_system.strategies.srd_pairs import (
    DEFAULT_SRD_PAIRS_ID,
    SRDPairsStrategy,
    SRDPairsStrategyConfig,
)
from trading_system.strategies.state import MarketState


def _stock(symbol: str, sector: str) -> Stock:
    return Stock(
        id=InstrumentId(f"{symbol}.PA"),
        symbol=symbol,
        exchange="PA",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin=f"FR{symbol:0>10}",
        sector=sector,
        country="FR",
    )


def _ranked(stock: Stock, score: str) -> ScoredStock:
    s = Decimal(score)
    return ScoredStock(
        stock=stock,
        score=s,
        breakdown=ScoreBreakdown(
            stability=s,
            yield_quality=s,
            valuation=s,
        ),
    )


def _constraints() -> PhaseConstraints:
    return PhaseConstraints(
        max_positions=10,
        max_trades_per_month=100,
        allocation_targets={
            AllocationBucket.STOCK: Decimal("0.90"),
            AllocationBucket.TACTICAL: Decimal("0.10"),
        },
        turbo_exposure_max=Decimal(0),
        risk_per_trade_band=(Decimal("0.005"), Decimal("0.02")),
        max_drawdown=Decimal("0.15"),
    )


def _state_with_ranking(ranking) -> MarketState:
    portfolio = Portfolio.empty(Money(Decimal("10000"), Currency.EUR))

    class _NullProvider:
        def bars(self, *_a, **_kw):
            from trading_system.result import Err

            return Err("data:not_supported")

        def latest(self, *_a, **_kw):
            from trading_system.result import Err

            return Err("data:not_supported")

        def dividends(self, *_a, **_kw):
            from trading_system.result import Err

            return Err("data:not_supported")

    return MarketState(
        at=datetime(2026, 5, 31, 12, tzinfo=UTC),
        portfolio=portfolio,
        constraints=_constraints(),
        regime=MarketRegime.SIDEWAYS,
        screener_ranking=tuple(ranking),
        market=_NullProvider(),
    )


# ---------------------------------------------------------------------------
# Signal logic
# ---------------------------------------------------------------------------


def test_emits_long_short_pair_on_sector_match():
    """Winner + loser in the same sector ⇒ both legs emitted."""
    winner = _stock("AAA", sector="tech")
    middle = _stock("BBB", sector="tech")
    loser = _stock("CCC", sector="tech")
    ranking = [
        _ranked(winner, "0.90"),
        _ranked(middle, "0.50"),
        _ranked(loser, "0.10"),
    ]
    state = _state_with_ranking(ranking)
    strategy = SRDPairsStrategy()
    proposals = strategy.evaluate(state)
    assert len(proposals) == 2
    long_leg = next(p for p in proposals if p.side is Side.BUY)
    short_leg = next(p for p in proposals if p.side is Side.SELL)
    assert long_leg.instrument == winner
    assert long_leg.order_type is OrderType.SRD_LONG
    assert short_leg.instrument == loser
    assert short_leg.order_type is OrderType.SRD_SHORT
    assert long_leg.source_strategy == DEFAULT_SRD_PAIRS_ID


def test_skips_when_no_sector_match():
    """Winner has no sector counterpart ⇒ empty list."""
    winner = _stock("AAA", sector="tech")
    other_sector = _stock("BBB", sector="energy")
    ranking = [
        _ranked(winner, "0.90"),
        _ranked(other_sector, "0.10"),
    ]
    state = _state_with_ranking(ranking)
    strategy = SRDPairsStrategy()
    assert strategy.evaluate(state) == []


def test_skips_when_score_gap_below_threshold():
    """Winner − loser score < min_ranking_gap ⇒ empty list."""
    winner = _stock("AAA", sector="tech")
    loser = _stock("BBB", sector="tech")
    ranking = [
        _ranked(winner, "0.55"),
        _ranked(loser, "0.50"),  # gap 0.05 < default 0.10
    ]
    state = _state_with_ranking(ranking)
    strategy = SRDPairsStrategy()
    assert strategy.evaluate(state) == []


def test_skips_when_ranking_too_short():
    """Need at least 2 stocks in the ranking."""
    winner = _stock("AAA", sector="tech")
    ranking = [_ranked(winner, "0.90")]
    state = _state_with_ranking(ranking)
    strategy = SRDPairsStrategy()
    assert strategy.evaluate(state) == []


def test_skips_when_equity_non_positive():
    """Defensive — empty portfolio is a no-op."""
    winner = _stock("AAA", sector="tech")
    loser = _stock("BBB", sector="tech")
    ranking = [_ranked(winner, "0.90"), _ranked(loser, "0.10")]
    state = _state_with_ranking(ranking)
    # Manually zero the portfolio's cash so equity is 0.
    state.portfolio._cash = Money(Decimal(0), Currency.EUR)  # type: ignore[attr-defined]
    strategy = SRDPairsStrategy()
    assert strategy.evaluate(state) == []


def test_proposal_size_matches_config():
    """REQ_F_SRD_002 — leg_size_pct_of_capital flows into the
    TradeProposal so the runtime sizes the SRD leg accordingly."""
    cfg = SRDPairsStrategyConfig(leg_size_pct_of_capital=Decimal("0.02"))
    winner = _stock("AAA", sector="tech")
    loser = _stock("BBB", sector="tech")
    ranking = [_ranked(winner, "0.90"), _ranked(loser, "0.10")]
    state = _state_with_ranking(ranking)
    strategy = SRDPairsStrategy(cfg=cfg)
    proposals = strategy.evaluate(state)
    assert len(proposals) == 2
    for proposal in proposals:
        assert proposal.size_pct_of_capital == Decimal("0.02")


def test_strategy_id_is_overridable():
    """Custom strategy_id flows into the proposals."""
    custom = StrategyId("alpha-srd-pair-v1")
    winner = _stock("AAA", sector="tech")
    loser = _stock("BBB", sector="tech")
    ranking = [_ranked(winner, "0.90"), _ranked(loser, "0.10")]
    state = _state_with_ranking(ranking)
    strategy = SRDPairsStrategy(strategy_id=custom)
    proposals = strategy.evaluate(state)
    assert all(p.source_strategy == custom for p in proposals)
