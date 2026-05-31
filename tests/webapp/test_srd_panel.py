"""CR-030 — SRD dashboard panel wiring tests.

REQ refs:
- REQ_F_SRD_003 / REQ_F_SRD_006 — SRDPositionRow surfaces every
  open SRD position with quantity / entry / mark / unrealized PnL /
  settlement_at / estimated CRD fee.
- REQ_F_SRD_005 — srd_settlements_count counter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.models.identifiers import InstrumentId, AccountId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.portfolio.portfolio import Portfolio
from trading_system.portfolio.srd_position import (
    SRDPosition,
    last_business_day_of_month,
)
from trading_system.result import Nothing, Some
from trading_system.webapp.paper_state_reader import (
    RuntimePaperStateReader,
    _srd_position_rows,
    _srd_settlements_count,
)


_T0 = datetime(2026, 5, 31, 12, tzinfo=UTC)
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


def _portfolio_with_srd_long() -> Portfolio:
    p = Portfolio.empty(Money(Decimal("10000"), Currency.EUR))
    entry_at = _T0 - timedelta(days=10)
    pos = SRDPosition(
        instrument=_AC,
        direction="LONG",
        quantity=Decimal(100),
        entry_price=Decimal(50),
        entry_at=entry_at,
        settlement_cycle=last_business_day_of_month(entry_at),
    )
    p._srd_positions[_AC.id] = pos  # type: ignore[attr-defined]
    p._last_prices[_AC.id] = Decimal(50)  # type: ignore[attr-defined]
    return p


def test_srd_position_rows_renders_position_with_estimated_crd_fee():
    """REQ_F_SRD_003 / REQ_F_SRD_006 — one row per open SRD
    position; estimated CRD fee = qty × entry × 0.25%/month."""
    portfolio = _portfolio_with_srd_long()
    rows = _srd_position_rows(portfolio)
    assert len(rows) == 1
    row = rows[0]
    assert row.instrument_symbol == "AC"
    assert row.direction == "LONG"
    assert row.quantity == Decimal(100)
    assert row.entry_price == Decimal(50)
    # 100 × 50 × 0.0025 = 12.50.
    assert row.estimated_crd_fee == Decimal("12.5000")
    # Mark = entry ⇒ unrealized = 0.00%.
    assert row.unrealized_pnl_pct == Decimal("0.00")
    # settlement_at is the last business day of the entry month.
    assert row.settlement_at.tzinfo is UTC


def test_srd_position_rows_reflects_mark_unrealized_pnl():
    """REQ_F_SRD_006 — latest mark drives the unrealized-pnl
    column; SHORT direction negates."""
    portfolio = _portfolio_with_srd_long()
    # Bump the mark price +10% ⇒ LONG unrealized +10.00%.
    portfolio._last_prices[_AC.id] = Decimal(55)  # type: ignore[attr-defined]
    rows = _srd_position_rows(portfolio)
    assert rows[0].unrealized_pnl_pct == Decimal("10.00")
    assert rows[0].latest_close == Decimal(55)

    # Add a SHORT position on a fresh instrument + mark up; the
    # short SHALL show a negative unrealized.
    bnp = Stock(
        id=InstrumentId("BNP.PA"),
        symbol="BNP",
        exchange="PA",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="FR0000131104",
        sector="financials",
        country="FR",
    )
    short_pos = SRDPosition(
        instrument=bnp,
        direction="SHORT",
        quantity=Decimal(100),
        entry_price=Decimal(50),
        entry_at=_T0 - timedelta(days=10),
        settlement_cycle=last_business_day_of_month(_T0 - timedelta(days=10)),
    )
    portfolio._srd_positions[bnp.id] = short_pos  # type: ignore[attr-defined]
    portfolio._last_prices[bnp.id] = Decimal(55)  # type: ignore[attr-defined]
    rows = _srd_position_rows(portfolio)
    by_symbol = {r.instrument_symbol: r for r in rows}
    assert by_symbol["BNP"].unrealized_pnl_pct == Decimal("-10.00")


def test_srd_position_rows_empty_when_no_srd_positions():
    portfolio = Portfolio.empty(Money(Decimal("10000"), Currency.EUR))
    assert _srd_position_rows(portfolio) == ()


def test_srd_settlements_count_starts_at_zero_grows_with_settlements():
    portfolio = _portfolio_with_srd_long()
    assert _srd_settlements_count(portfolio) == 0
    # Settle one position via the close path.
    from trading_system.tax.config import TaxConfig
    portfolio.apply_srd_close(
        instrument_id=_AC.id,
        settlement_price=Decimal(55),
        crd_fee=Decimal("12.50"),
        settlement_at=_T0,
        tax=TaxConfig.default(),
    )
    assert _srd_settlements_count(portfolio) == 1


def test_paper_state_response_carries_srd_position_rows():
    """End-to-end: the reader's snapshot carries the SRD rows
    on the response. Defensive against a stub runtime without
    `runtime.universe` / etc."""

    @dataclass
    class _Registry:
        runtimes: dict = field(default_factory=dict)

        def status(self, aid):
            r = self.runtimes.get(aid)
            return Some(r) if r is not None else Nothing()

    portfolio = _portfolio_with_srd_long()

    class _Runtime:
        pass

    runtime = _Runtime()
    runtime.portfolio = portfolio
    runtime.universe = ()
    runtime.instrument = _AC
    runtime.bar_source = None
    runtime.market_data_provider = None
    runtime.is_alive = lambda: True
    runtime.is_degraded = lambda: False
    runtime.degraded_since = lambda: None
    runtime.last_tick_at = lambda: _T0
    runtime.equity_history = lambda: ()
    runtime.session = None
    runtime.latest_close = lambda: None

    aid = AccountId("paper-2026-05-30T12:00:00+00:00")
    reader = RuntimePaperStateReader(
        registry=_Registry(runtimes={aid: runtime}),
        cache_ttl_seconds=0,  # bypass cache for the test
    )
    snap = reader.paper_state(account_id=aid, as_of=_T0)
    assert len(snap.srd_positions) == 1
    assert snap.srd_positions[0].instrument_symbol == "AC"
    assert snap.srd_settlements_count == 0
