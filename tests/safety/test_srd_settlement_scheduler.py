"""CR-030 / TC_SRD_007..012 — SRD settlement scheduler.

REQ refs:
- REQ_F_SRD_005 — scheduler fires on last business day; books
  cash settlement + CRD fee + optional rollover.
- REQ_F_SRD_007 — tax engine integration; PFU 30% on net_pnl.
- REQ_NF_SRD_001 — paired-replay determinism.
- REQ_SDD_SRD_006 — tick(at) algorithm + atomic rollover.
- REQ_SDD_SRD_007 — CRD-fee + rollover-fee formula.
- REQ_SDD_SRD_010 — paired-run conformance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.data.types import Bar, Timeframe
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.trading import (
    Order,
    OrderType,
    Side,
    StopLoss,
    Trade,
    set_srd_eligible_instruments,
)
from trading_system.portfolio.portfolio import Portfolio
from trading_system.portfolio.srd_position import (
    SRDPosition,
    last_business_day_of_month,
)
from trading_system.result import Err, Ok
from trading_system.safety.srd_settlement_scheduler import (
    SRDSettlementCalendar,
    SRDSettlementScheduler,
)
from trading_system.tax.config import TaxConfig


_T0 = datetime(2026, 5, 31, 12, tzinfo=UTC)  # Sunday — calendar end.
_SETTLEMENT_DAY = datetime(2026, 5, 29, 12, tzinfo=UTC)  # Friday.
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
_AI = Stock(
    id=InstrumentId("AI.PA"),
    symbol="AI",
    exchange="PA",
    currency=Currency.EUR,
    cls=InstrumentClass.STOCK,
    isin="FR0000120073",
    sector="industrials",
    country="FR",
)
_BNP = Stock(
    id=InstrumentId("BNP.PA"),
    symbol="BNP",
    exchange="PA",
    currency=Currency.EUR,
    cls=InstrumentClass.STOCK,
    isin="FR0000131104",
    sector="financials",
    country="FR",
)


def _eur(s: str) -> Money:
    return Money(Decimal(s), Currency.EUR)


def _stop() -> StopLoss:
    return StopLoss(price=Decimal("40"))


def _open_srd(
    portfolio: Portfolio,
    *,
    instrument: Stock,
    direction: str = "LONG",
    quantity: Decimal = Decimal(100),
    entry_price: Decimal = Decimal(50),
    entry_at: datetime = _SETTLEMENT_DAY - timedelta(days=15),
) -> None:
    """Inject an SRD position directly (skipping the
    apply_srd_open path so the test fixture stays small)."""
    pos = SRDPosition(
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        quantity=quantity,
        entry_price=entry_price,
        entry_at=entry_at,
        settlement_cycle=last_business_day_of_month(entry_at),
    )
    portfolio._srd_positions[instrument.id] = pos  # type: ignore[attr-defined]
    portfolio._last_prices[instrument.id] = entry_price  # type: ignore[attr-defined]


@dataclass
class _StubProvider:
    """Returns a pinned settlement-day Bar per instrument id."""

    prices: dict[InstrumentId, Decimal] = field(default_factory=dict)

    def bars(self, instrument, timeframe, start, end):
        del timeframe, start, end
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


# ---------------------------------------------------------------------------
# TC_SRD_007 — non-settlement-day no-op
# ---------------------------------------------------------------------------


def test_scheduler_tick_non_settlement_day_returns_empty():
    """REQ_F_SRD_005 / REQ_SDD_SRD_006 — `tick(at)` on a non-
    settlement day SHALL return `Ok([])` + leave the portfolio
    untouched."""
    portfolio = Portfolio.empty(_eur("10000"))
    scheduler = SRDSettlementScheduler(
        portfolio=portfolio,
        provider=_StubProvider(),
    )
    # Wednesday May 13 2026 — not a settlement day.
    mid_month = datetime(2026, 5, 13, 12, tzinfo=UTC)
    result = scheduler.tick(mid_month)
    assert isinstance(result, Ok)
    assert result.value == []
    assert portfolio.srd_settlement_rows() == []


# ---------------------------------------------------------------------------
# TC_SRD_008 — settlement-day golden cycle (3 positions)
# ---------------------------------------------------------------------------


def test_scheduler_tick_settlement_day_golden_cycle():
    """REQ_F_SRD_005 / REQ_F_SRD_007 / REQ_SDD_SRD_006 — 3 SRD
    positions on the same settlement day:
      (1) LONG AC.PA  qty=100 entry=50 settle=60  ⇒ gross  +1000;
      (2) LONG AI.PA  qty=100 entry=50 settle=45  ⇒ gross   -500;
      (3) SHORT BNP   qty=100 entry=50 settle=42  ⇒ gross   +800.
    CRD fee 0.25% × notional per position = 12.50 each.
    """
    portfolio = Portfolio.empty(_eur("10000"))
    _open_srd(portfolio, instrument=_AC, direction="LONG")
    _open_srd(portfolio, instrument=_AI, direction="LONG")
    _open_srd(portfolio, instrument=_BNP, direction="SHORT")

    provider = _StubProvider(
        prices={
            _AC.id: Decimal(60),
            _AI.id: Decimal(45),
            _BNP.id: Decimal(42),
        }
    )
    scheduler = SRDSettlementScheduler(
        portfolio=portfolio,
        provider=provider,
    )
    result = scheduler.tick(_SETTLEMENT_DAY)
    assert isinstance(result, Ok), getattr(result, "error", "ok")
    settlements = result.value
    assert len(settlements) == 3
    # Lex-ordered by instrument id (AC.PA, AI.PA, BNP.PA).
    assert [s.instrument.id for s in settlements] == [
        _AC.id,
        _AI.id,
        _BNP.id,
    ]
    # Position #1 (LONG AC.PA gain +1000).
    s_ac = settlements[0]
    assert s_ac.gross_pnl == Decimal(1000)
    assert s_ac.crd_fee == Decimal("12.50")
    assert s_ac.net_pnl == Decimal("987.50")
    # PFU 30% on 987.50 = 296.25.
    assert s_ac.tax == Decimal("296.25")
    # Position #2 (LONG AI.PA loss -500).
    s_ai = settlements[1]
    assert s_ai.gross_pnl == Decimal(-500)
    assert s_ai.crd_fee == Decimal("12.50")
    assert s_ai.net_pnl == Decimal("-512.50")
    assert s_ai.tax == Decimal(0)  # losses pass through
    # Position #3 (SHORT BNP gain +800).
    s_bnp = settlements[2]
    assert s_bnp.gross_pnl == Decimal(800)
    assert s_bnp.crd_fee == Decimal("12.50")
    assert s_bnp.net_pnl == Decimal("787.50")
    assert s_bnp.tax == Decimal("236.25")
    # All positions closed.
    assert portfolio.srd_positions() == {}
    # Audit rows tagged source.
    assert all(s.source == "srd_settlement" for s in settlements)


# ---------------------------------------------------------------------------
# TC_SRD_009 — auto_rollover
# ---------------------------------------------------------------------------


def test_scheduler_tick_auto_rollover_opens_new_position():
    """REQ_F_SRD_005 / REQ_SDD_SRD_006 — auto_rollover=True ⇒
    close at settlement_price + immediately open a fresh
    SRDPosition for next month's settlement_cycle. Rollover fee
    charged on the new leg."""
    portfolio = Portfolio.empty(_eur("10000"))
    entry_at = _SETTLEMENT_DAY - timedelta(days=15)
    rolling = SRDPosition(
        instrument=_AC,
        direction="LONG",
        quantity=Decimal(100),
        entry_price=Decimal(50),
        entry_at=entry_at,
        settlement_cycle=last_business_day_of_month(entry_at),
        auto_rollover=True,
    )
    portfolio._srd_positions[_AC.id] = rolling  # type: ignore[attr-defined]
    portfolio._last_prices[_AC.id] = Decimal(50)  # type: ignore[attr-defined]

    provider = _StubProvider(prices={_AC.id: Decimal(60)})
    scheduler = SRDSettlementScheduler(
        portfolio=portfolio,
        provider=provider,
    )
    result = scheduler.tick(_SETTLEMENT_DAY)
    assert isinstance(result, Ok)
    [settlement] = result.value
    assert settlement.rolled_over is True
    # Rollover fee charged at 0.10% × 100 × 50 = 5.00.
    assert settlement.rollover_fee == Decimal("5.00")
    # Net = gross 1000 − crd 12.50 − rollover 5.00 = 982.50.
    assert settlement.net_pnl == Decimal("982.50")
    # New position is open at settlement_price for next month.
    new_pos = portfolio.srd_positions().get(_AC.id)
    assert new_pos is not None
    assert new_pos.entry_price == Decimal(60)
    assert new_pos.entry_at == _SETTLEMENT_DAY
    # June 30 2026 is Tuesday — last business day.
    assert new_pos.settlement_cycle.date() == datetime(2026, 6, 30).date()


# ---------------------------------------------------------------------------
# Coverage: provider failure aborts the tick
# ---------------------------------------------------------------------------


def test_scheduler_tick_provider_failure_aborts_tick():
    """REQ_SDD_SRD_006 — provider can't price ⇒ entire tick
    aborts with Err. No partial settlement."""
    portfolio = Portfolio.empty(_eur("10000"))
    _open_srd(portfolio, instrument=_AC)
    _open_srd(portfolio, instrument=_AI)
    # Provider only knows AC.PA — AI.PA lookup fails.
    provider = _StubProvider(prices={_AC.id: Decimal(60)})
    scheduler = SRDSettlementScheduler(
        portfolio=portfolio,
        provider=provider,
    )
    result = scheduler.tick(_SETTLEMENT_DAY)
    assert isinstance(result, Err)
    assert "srd:settlement_price_unavailable" in result.error


# ---------------------------------------------------------------------------
# TC_SRD_012 — paired-replay determinism
# ---------------------------------------------------------------------------


def _build_paired_fixture():
    portfolio = Portfolio.empty(_eur("10000"))
    _open_srd(portfolio, instrument=_AC, direction="LONG")
    _open_srd(portfolio, instrument=_BNP, direction="SHORT")
    return portfolio


def test_scheduler_tick_paired_replay_byte_identical():
    """REQ_NF_SRD_001 / REQ_SDD_SRD_010 — two identical fixtures
    + the same provider state produce tuple-equal settlements."""
    p1 = _build_paired_fixture()
    p2 = _build_paired_fixture()
    provider = _StubProvider(
        prices={_AC.id: Decimal(60), _BNP.id: Decimal(42)}
    )
    s1 = SRDSettlementScheduler(portfolio=p1, provider=provider)
    s2 = SRDSettlementScheduler(portfolio=p2, provider=provider)
    r1 = s1.tick(_SETTLEMENT_DAY)
    r2 = s2.tick(_SETTLEMENT_DAY)
    assert isinstance(r1, Ok) and isinstance(r2, Ok)
    assert r1.value == r2.value
    # Audit ledger also byte-identical.
    assert p1.srd_settlement_rows() == p2.srd_settlement_rows()


# ---------------------------------------------------------------------------
# SRDSettlementCalendar — settlement-day detection
# ---------------------------------------------------------------------------


def test_calendar_is_settlement_day_friday_when_sunday_is_month_end():
    """REQ_SDD_SRD_006 — May 31 2026 is a Sunday; settlement day
    SHALL be Friday May 29."""
    cal = SRDSettlementCalendar()
    assert cal.is_settlement_day(_SETTLEMENT_DAY)
    # Saturday May 30 is NOT a settlement day.
    sat = datetime(2026, 5, 30, 12, tzinfo=UTC)
    assert not cal.is_settlement_day(sat)


def test_calendar_honours_holidays():
    """REQ_SDD_SRD_003 — operator-supplied holidays roll back
    settlement day."""
    from datetime import date

    cal = SRDSettlementCalendar(holidays=frozenset({date(2026, 5, 29)}))
    # With May 29 marked as holiday, settlement day rolls to
    # Thursday May 28.
    thursday = datetime(2026, 5, 28, 12, tzinfo=UTC)
    assert cal.is_settlement_day(thursday)
    # The original Friday is no longer the settlement day.
    assert not cal.is_settlement_day(_SETTLEMENT_DAY)
