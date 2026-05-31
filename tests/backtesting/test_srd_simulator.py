"""CR-030 — SRDSimulator (backtest wrapper) tests.

REQ refs:
- REQ_F_SRD_005 (backtest integration).
- REQ_NF_SRD_001 — paired-replay determinism propagates through
  the wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.backtesting.srd_simulator import SRDSimulator
from trading_system.data.types import Bar
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.portfolio.portfolio import Portfolio
from trading_system.portfolio.srd_position import (
    SRDPosition,
    last_business_day_of_month,
)
from trading_system.result import Err, Ok
from trading_system.safety.srd_settlement_scheduler import (
    SRDSettlementScheduler,
)


_T0 = datetime(2026, 5, 31, 12, tzinfo=UTC)
_SETTLEMENT_DAY = datetime(2026, 5, 29, 12, tzinfo=UTC)
_AC = Stock(
    id=InstrumentId("AC.PA"),
    symbol="AC",
    exchange="PA",
    currency=Currency.EUR,
    cls=InstrumentClass.STOCK,
    isin="FR0000120404",
    sector="consumer-discretionary",
    country="FR",
)


@dataclass
class _StubProvider:
    prices: dict = field(default_factory=dict)

    def bars(self, instrument, _tf, _start, _end):
        price = self.prices.get(instrument.id)
        if price is None:
            return Err(f"data:not_found:{instrument.id}")
        bar = Bar(
            at=_SETTLEMENT_DAY,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal(1000),
        )
        return Ok([bar])

    def latest(self, instrument):
        return self.bars(instrument, None, None, None)

    def dividends(self, *_a, **_k):
        return Err("data:not_supported")


def _portfolio_with_position(qty: Decimal, entry: Decimal) -> Portfolio:
    portfolio = Portfolio.empty(Money(Decimal("10000"), Currency.EUR))
    entry_at = _SETTLEMENT_DAY - timedelta(days=10)
    pos = SRDPosition(
        instrument=_AC,
        direction="LONG",
        quantity=qty,
        entry_price=entry,
        entry_at=entry_at,
        settlement_cycle=last_business_day_of_month(entry_at),
    )
    portfolio._srd_positions[_AC.id] = pos  # type: ignore[attr-defined]
    portfolio._last_prices[_AC.id] = entry  # type: ignore[attr-defined]
    return portfolio


def test_simulator_tick_on_non_settlement_day_is_noop():
    """REQ_F_SRD_005 — non-settlement day ⇒ Ok([]) + nothing
    captured on the simulator's ledger."""
    portfolio = _portfolio_with_position(Decimal(100), Decimal(50))
    scheduler = SRDSettlementScheduler(
        portfolio=portfolio,
        provider=_StubProvider(prices={_AC.id: Decimal(60)}),
    )
    sim = SRDSimulator(scheduler=scheduler)
    mid_month = datetime(2026, 5, 13, 12, tzinfo=UTC)
    result = sim.tick(mid_month)
    assert isinstance(result, Ok)
    assert result.value == []
    assert sim.settlements_so_far() == ()


def test_simulator_tick_on_settlement_day_captures_rows():
    """REQ_F_SRD_005 — settlement-day tick books rows via the
    wrapped scheduler + captures them on the simulator's ledger."""
    portfolio = _portfolio_with_position(Decimal(100), Decimal(50))
    scheduler = SRDSettlementScheduler(
        portfolio=portfolio,
        provider=_StubProvider(prices={_AC.id: Decimal(60)}),
    )
    sim = SRDSimulator(scheduler=scheduler)
    result = sim.tick(_SETTLEMENT_DAY)
    assert isinstance(result, Ok)
    assert len(result.value) == 1
    assert sim.settlements_so_far() == tuple(result.value)


def test_simulator_paired_replay_byte_identical():
    """REQ_NF_SRD_001 — paired replay against the same fixture
    produces tuple-equal settlements + ledger."""
    p1 = _portfolio_with_position(Decimal(100), Decimal(50))
    p2 = _portfolio_with_position(Decimal(100), Decimal(50))
    provider = _StubProvider(prices={_AC.id: Decimal(60)})
    sim1 = SRDSimulator(
        scheduler=SRDSettlementScheduler(portfolio=p1, provider=provider)
    )
    sim2 = SRDSimulator(
        scheduler=SRDSettlementScheduler(portfolio=p2, provider=provider)
    )
    r1 = sim1.tick(_SETTLEMENT_DAY)
    r2 = sim2.tick(_SETTLEMENT_DAY)
    assert isinstance(r1, Ok) and isinstance(r2, Ok)
    assert r1.value == r2.value
    assert sim1.settlements_so_far() == sim2.settlements_so_far()


def test_simulator_propagates_scheduler_err():
    """Provider failure ⇒ Err propagates through the wrapper +
    the simulator's ledger stays empty."""
    portfolio = _portfolio_with_position(Decimal(100), Decimal(50))
    scheduler = SRDSettlementScheduler(
        portfolio=portfolio,
        provider=_StubProvider(prices={}),  # AC.PA price missing
    )
    sim = SRDSimulator(scheduler=scheduler)
    result = sim.tick(_SETTLEMENT_DAY)
    assert isinstance(result, Err)
    assert "srd:settlement_price_unavailable" in result.error
    assert sim.settlements_so_far() == ()
