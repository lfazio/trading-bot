"""Shared fixtures for strategies tests.

Defines:
- ``StubPortfolioView`` — minimal in-memory ``PortfolioView`` test
  double with mutable holdings + exposures for parametrization.
- ``StubMarketProvider`` — programmable ``MarketDataProvider`` that
  returns canned bar series and the latest tick.
- factory helpers: ``make_stock``, ``make_scored_stock``,
  ``make_phase_constraints``, ``make_state``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from trading_system.data.types import Bar, Fundamentals, Timeframe
from trading_system.execution.fees import FlatFeeModel
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import Instrument, InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.phase import (
    AllocationBucket,
    MarketRegime,
    PhaseConstraints,
)
from trading_system.models.trading import Dividend, Position
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.screener.engine import ScoreBreakdown, ScoredStock
from trading_system.strategies.state import MarketState
from trading_system.tax.config import TaxConfig

EUR = Currency.EUR


# ---------------------------------------------------------------------------
# Stocks / scored stocks
# ---------------------------------------------------------------------------


def make_stock(symbol: str = "ABC", isin_suffix: str = "0000") -> Stock:
    return Stock(
        id=InstrumentId(f"id-{symbol}"),
        symbol=symbol,
        exchange="EPA",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin=f"FR000{isin_suffix}",
        sector="Industrials",
        country="FR",
    )


def make_scored_stock(
    stock: Stock | None = None,
    score: str = "0.5",
) -> ScoredStock:
    return ScoredStock(
        stock=stock or make_stock(),
        score=Decimal(score),
        breakdown=ScoreBreakdown(
            stability=Decimal("0.5"),
            yield_quality=Decimal("0.5"),
            valuation=Decimal("0.5"),
        ),
    )


# ---------------------------------------------------------------------------
# Portfolio view stub
# ---------------------------------------------------------------------------


class StubPortfolioView:
    """In-memory ``PortfolioView`` test double."""

    def __init__(
        self,
        equity_amount: str = "10000",
        cash_amount: str = "10000",
        exposures: dict[AllocationBucket, Decimal] | None = None,
        positions: dict[InstrumentId, Position] | None = None,
    ) -> None:
        self._equity = Money(Decimal(equity_amount), EUR)
        self._cash = Money(Decimal(cash_amount), EUR)
        self._exposures: dict[AllocationBucket, Decimal] = exposures or {}
        self._positions: dict[InstrumentId, Position] = positions or {}

    def equity(self) -> Money:
        return self._equity

    def cash(self) -> Money:
        return self._cash

    def exposure_pct(self, bucket: AllocationBucket) -> Decimal:
        return self._exposures.get(bucket, Decimal(0))

    def holds(self, instrument_id: InstrumentId) -> bool:
        return instrument_id in self._positions

    def position_for(self, instrument_id: InstrumentId) -> Option[Position]:
        pos = self._positions.get(instrument_id)
        return Some(pos) if pos is not None else Nothing()


# ---------------------------------------------------------------------------
# Market data provider stub
# ---------------------------------------------------------------------------


class StubMarketProvider:
    """Programmable ``MarketDataProvider`` for strategy tests."""

    def __init__(self) -> None:
        self.bars_map: dict[InstrumentId, list[Bar]] = {}
        self.latest_map: dict[InstrumentId, Bar] = {}
        self.fundamentals_map: dict[InstrumentId, Fundamentals] = {}

    def bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Result[list[Bar], str]:
        bars = self.bars_map.get(instrument.id)
        if bars is None:
            return Err("data:not_found")
        return Ok([b for b in bars if start <= b.at <= end])

    def latest(self, instrument: Instrument) -> Result[Bar, str]:
        bar = self.latest_map.get(instrument.id)
        if bar is None:
            return Err("data:not_found")
        return Ok(bar)

    def dividends(self, instrument: Instrument, year: int) -> Result[list[Dividend], str]:
        return Ok([])

    def fundamentals(self, instrument: Instrument) -> Result[Fundamentals, str]:
        f = self.fundamentals_map.get(instrument.id)
        return Ok(f) if f is not None else Err("data:not_found")


# ---------------------------------------------------------------------------
# PhaseConstraints / MarketState
# ---------------------------------------------------------------------------


def make_phase_constraints(**overrides: Any) -> PhaseConstraints:
    base: dict[str, Any] = {
        "max_positions": 6,
        "max_trades_per_month": 8,
        "allocation_targets": {
            AllocationBucket.STOCK: Decimal("0.70"),
            AllocationBucket.TACTICAL: Decimal("0.30"),
        },
        "turbo_exposure_max": Decimal("0.05"),
        "risk_per_trade_band": (Decimal("0.01"), Decimal("0.02")),
        "max_drawdown": Decimal("0.15"),
        "portfolio_vol_cap": None,
    }
    base.update(overrides)
    return PhaseConstraints(**base)


def make_state(  # noqa: PLR0913 - test factory; mirrors MarketState's fields
    *,
    portfolio: StubPortfolioView | None = None,
    constraints: PhaseConstraints | None = None,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
    screener_ranking: tuple[ScoredStock, ...] = (),
    market: StubMarketProvider | None = None,
    at: datetime = datetime(2026, 5, 1, 16, 0),
) -> MarketState:
    return MarketState(
        at=at,
        portfolio=portfolio or StubPortfolioView(),
        constraints=constraints or make_phase_constraints(),
        regime=regime,
        screener_ranking=screener_ranking,
        market=market or StubMarketProvider(),
    )


# ---------------------------------------------------------------------------
# Default fee + tax models for proposal estimation
# ---------------------------------------------------------------------------


def make_fee_model() -> FlatFeeModel:
    return FlatFeeModel(
        commission=Money(Decimal("1.00"), EUR),
        spread_bps=Decimal(5),
    )


def make_tax_config() -> TaxConfig:
    return TaxConfig.default()


# ---------------------------------------------------------------------------
# Bar synthesis — deterministic, used by tactical signal tests
# ---------------------------------------------------------------------------


def synthetic_bars(
    *,
    base_price: str = "100",
    delta: str = "0.5",
    count: int = 60,
    end_at: datetime = datetime(2026, 5, 1),
) -> list[Bar]:
    """Linearly increasing closes ending on ``end_at`` — guarantees
    an uptrend that overlaps the default ``MarketState.at``."""
    bars: list[Bar] = []
    price = Decimal(base_price)
    step = Decimal(delta)
    start = end_at - timedelta(days=count - 1)
    for i in range(count):
        close = price + step * Decimal(i)
        bars.append(
            Bar(
                at=start + timedelta(days=i),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=Decimal(1000),
            )
        )
    return bars
