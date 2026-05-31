"""CR-030 / TC_SRD_004 + TC_SRD_005 + TC_SRD_006 + TC_SRD_010 —
Portfolio SRD accounting + coverage gate.

REQ refs:
- REQ_F_SRD_003 — SRDPosition + Portfolio.srd_positions.
- REQ_F_SRD_004 — srd_coverage_gate (25% floor, DEGRADED at 30%).
- REQ_F_SRD_006 — mark-to-market + early liquidation.
- REQ_SDD_SRD_004 — Portfolio apply_srd_open/close.
- REQ_SDD_SRD_005 — coverage gate formula.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest

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
from trading_system.risk.srd_gate import (
    DEFAULT_MIN_COVERAGE_RATIO,
    DEFAULT_WARN_COVERAGE_RATIO,
    EQUITY_COVERAGE_HAIRCUT,
    srd_coverage_gate,
)
from trading_system.result import Err, Ok
from trading_system.tax.config import TaxConfig


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
_NON_ELIGIBLE = Stock(
    id=InstrumentId("ASML.AS"),
    symbol="ASML",
    exchange="AS",
    currency=Currency.EUR,
    cls=InstrumentClass.STOCK,
    isin="NL0010273215",
    sector="tech",
    country="NL",
)


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), Currency.EUR)


def _stop() -> StopLoss:
    return StopLoss(price=Decimal("40"))


def _srd_order(
    *,
    instrument: Stock,
    qty: Decimal,
    type_: OrderType = OrderType.SRD_LONG,
    order_id: str = "ord-1",
) -> Order:
    return Order(
        id=OrderId(order_id),
        instrument=instrument,
        side=Side.BUY if type_ is OrderType.SRD_LONG else Side.SELL,
        quantity=qty,
        type=type_,
        stop_loss=_stop(),
        created_at=_T0,
        source_strategy=StrategyId("test"),
    )


def _fill(*, qty: Decimal, price: Decimal, order_id: str = "ord-1") -> Trade:
    return Trade(
        id=TradeId(f"trd-{order_id}"),
        order_id=OrderId(order_id),
        executed_at=_T0,
        price=price,
        quantity_filled=qty,
        fees=_eur("0"),
    )


# ---------------------------------------------------------------------------
# TC_SRD_004 — Portfolio.apply_srd_open / apply_srd_close
# ---------------------------------------------------------------------------


def test_portfolio_apply_srd_open_creates_position_with_unchanged_cash():
    """REQ_F_SRD_003 / REQ_SDD_SRD_004 — open SRD position; cash
    UNCHANGED (deferred settlement)."""
    set_srd_eligible_instruments({_AC.id})
    try:
        portfolio = Portfolio.empty(_eur("10000"))
        order = _srd_order(instrument=_AC, qty=Decimal(100))
        trade = _fill(qty=Decimal(100), price=Decimal(50))
        portfolio.apply_srd_open(order, trade)
        assert _AC.id in portfolio.srd_positions()
        pos = portfolio.srd_positions()[_AC.id]
        assert pos.direction == "LONG"
        assert pos.quantity == Decimal(100)
        assert pos.entry_price == Decimal(50)
        # Cash UNCHANGED at open.
        assert portfolio.cash() == _eur("10000")
    finally:
        set_srd_eligible_instruments(())


def test_portfolio_apply_srd_open_duplicate_raises():
    """REQ_SDD_SRD_004 — second open on the same instrument raises."""
    set_srd_eligible_instruments({_AC.id})
    try:
        portfolio = Portfolio.empty(_eur("10000"))
        order = _srd_order(instrument=_AC, qty=Decimal(100))
        trade = _fill(qty=Decimal(100), price=Decimal(50))
        portfolio.apply_srd_open(order, trade)
        with pytest.raises(ValueError, match="already open"):
            portfolio.apply_srd_open(order, trade)
    finally:
        set_srd_eligible_instruments(())


def test_portfolio_apply_srd_close_books_settlement_long_gain():
    """REQ_F_SRD_007 / REQ_SDD_SRD_008 — LONG with €10/share gain;
    PFU applied to positive net_pnl; cash credited by
    (net_pnl − tax)."""
    set_srd_eligible_instruments({_AC.id})
    try:
        portfolio = Portfolio.empty(_eur("10000"))
        order = _srd_order(instrument=_AC, qty=Decimal(100))
        trade = _fill(qty=Decimal(100), price=Decimal(50))
        portfolio.apply_srd_open(order, trade)
        # Settle at €60 (+€10/share, qty=100 ⇒ gross_pnl=1000).
        settlement = portfolio.apply_srd_close(
            instrument_id=_AC.id,
            settlement_price=Decimal(60),
            crd_fee=Decimal("12.50"),  # 100 × 50 × 0.0025
            settlement_at=_T0,
            tax=TaxConfig.default(),
        )
        # Position closed.
        assert _AC.id not in portfolio.srd_positions()
        # Settlement values: gross 1 000, fee 12.50, net 987.50.
        assert settlement.gross_pnl == Decimal(1000)
        assert settlement.crd_fee == Decimal("12.50")
        assert settlement.net_pnl == Decimal("987.50")
        # PFU 30% on net_pnl ⇒ tax = 296.25.
        assert settlement.tax == Decimal("296.25")
        # Cash credited by (net_pnl − tax) = 987.50 − 296.25 = 691.25.
        assert portfolio.cash() == _eur("10691.25")
        # Audit row tagged source.
        assert settlement.source == "srd_settlement"
        assert portfolio.srd_settlement_rows() == [settlement]
    finally:
        set_srd_eligible_instruments(())


def test_portfolio_apply_srd_close_short_loss_no_tax():
    """REQ_F_SRD_007 — SHORT loss ⇒ net_pnl negative; no tax
    (losses pass through gross)."""
    set_srd_eligible_instruments({_AC.id})
    try:
        portfolio = Portfolio.empty(_eur("10000"))
        # SHORT @ 50; settle @ 60 ⇒ loss of 10/share × 100 = -1000.
        order = _srd_order(
            instrument=_AC, qty=Decimal(100), type_=OrderType.SRD_SHORT
        )
        trade = _fill(qty=Decimal(100), price=Decimal(50))
        portfolio.apply_srd_open(order, trade)
        settlement = portfolio.apply_srd_close(
            instrument_id=_AC.id,
            settlement_price=Decimal(60),
            crd_fee=Decimal("12.50"),
            settlement_at=_T0,
            tax=TaxConfig.default(),
        )
        assert settlement.gross_pnl == Decimal(-1000)
        assert settlement.net_pnl == Decimal("-1012.50")
        assert settlement.tax == Decimal(0)
        # Cash debited by 1012.50.
        assert portfolio.cash() == _eur("8987.50")
    finally:
        set_srd_eligible_instruments(())


def test_portfolio_mark_covers_srd_positions_via_equity_after_tax():
    """REQ_F_SRD_006 / REQ_SDD_SRD_004 — Portfolio.mark refreshes
    BOTH cash and SRD positions; equity_after_tax reflects SRD
    unrealized PnL mid-month."""
    set_srd_eligible_instruments({_AC.id})
    try:
        portfolio = Portfolio.empty(_eur("10000"))
        order = _srd_order(instrument=_AC, qty=Decimal(100))
        trade = _fill(qty=Decimal(100), price=Decimal(50))
        portfolio.apply_srd_open(order, trade)
        # Before mark: equity == cash (no realized; entry price seeded).
        # After marking at €55 (+€5/share, qty=100 ⇒ +500 unrealized):
        portfolio.mark({_AC.id: Decimal(55)})
        assert portfolio.equity_after_tax().amount == Decimal(10500)
        # Marking back to entry price ⇒ unrealized PnL goes back to 0.
        portfolio.mark({_AC.id: Decimal(50)})
        assert portfolio.equity_after_tax().amount == Decimal(10000)
    finally:
        set_srd_eligible_instruments(())


# ---------------------------------------------------------------------------
# TC_SRD_005 — srd_coverage_gate happy + insufficient
# ---------------------------------------------------------------------------


def test_srd_coverage_gate_happy_path_accepts():
    """REQ_F_SRD_004 / REQ_SDD_SRD_005 — coverage well above the
    25% floor ⇒ Ok(None)."""
    set_srd_eligible_instruments({_AC.id})
    try:
        portfolio = Portfolio.empty(_eur("5000"))
        proposal = _srd_order(instrument=_AC, qty=Decimal(100))
        prices = {_AC.id: Decimal(50)}
        # Notional after = 100 × 50 = 5000; coverage = 5000 cash.
        # ratio = 1.0 >> 0.25 floor.
        result = srd_coverage_gate(
            portfolio, proposal, prices, safety=None
        )
        assert isinstance(result, Ok)
    finally:
        set_srd_eligible_instruments(())


def test_srd_coverage_gate_insufficient_coverage_rejects():
    """REQ_F_SRD_004 — coverage below the 25% floor ⇒ Err with
    categorised reason."""
    set_srd_eligible_instruments({_AC.id})
    try:
        # Portfolio with only €500 cash; proposed 100 × €40 = €4000
        # notional. ratio = 500/4000 = 0.125 < 0.25.
        portfolio = Portfolio.empty(_eur("500"))
        proposal = _srd_order(instrument=_AC, qty=Decimal(100))
        prices = {_AC.id: Decimal(40)}
        result = srd_coverage_gate(
            portfolio, proposal, prices, safety=None
        )
        assert isinstance(result, Err)
        assert result.error.startswith("srd:insufficient_coverage:")
    finally:
        set_srd_eligible_instruments(())


def test_srd_coverage_gate_non_srd_order_passes_through():
    """REQ_SDD_SRD_005 — non-SRD proposals SHALL pass through the
    gate without any check."""
    set_srd_eligible_instruments({_AC.id})
    try:
        portfolio = Portfolio.empty(_eur("100"))  # tiny cash
        proposal = Order(
            id=OrderId("ord-1"),
            instrument=_AC,
            side=Side.BUY,
            quantity=Decimal(100),
            type=OrderType.MARKET,
            stop_loss=_stop(),
            created_at=_T0,
            source_strategy=StrategyId("test"),
        )
        prices = {_AC.id: Decimal(50)}
        result = srd_coverage_gate(portfolio, proposal, prices)
        assert isinstance(result, Ok)
    finally:
        set_srd_eligible_instruments(())


# ---------------------------------------------------------------------------
# TC_SRD_006 — DEGRADED warning band (25%-30%)
# ---------------------------------------------------------------------------


@dataclass
class _SpySafety:
    triggers: list = field(default_factory=list)

    def raise_trigger(self, trigger):
        self.triggers.append(trigger)


def test_srd_coverage_gate_warning_band_accepts_with_degraded_trigger():
    """REQ_F_SRD_004 / REQ_SDD_SRD_005 — coverage between 25% and
    30% ⇒ Ok BUT SafetyLayer.raise_trigger gets a DEGRADED
    trigger."""
    set_srd_eligible_instruments({_AC.id})
    try:
        # Portfolio cash = 1100; notional = 100 × 40 = 4000; ratio =
        # 0.275 ⇒ between floor (0.25) and warn (0.30).
        portfolio = Portfolio.empty(_eur("1100"))
        proposal = _srd_order(instrument=_AC, qty=Decimal(100))
        prices = {_AC.id: Decimal(40)}
        spy = _SpySafety()
        result = srd_coverage_gate(
            portfolio, proposal, prices, safety=spy
        )
        assert isinstance(result, Ok)
        assert len(spy.triggers) == 1
        trigger = spy.triggers[0]
        assert trigger.code == "srd_coverage_low"
        assert trigger.severity == "DEGRADED"
    finally:
        set_srd_eligible_instruments(())


def test_srd_coverage_gate_well_above_warn_no_trigger():
    """REQ_F_SRD_004 — coverage well above the 30% warn threshold
    ⇒ Ok + SafetyLayer NOT called."""
    set_srd_eligible_instruments({_AC.id})
    try:
        portfolio = Portfolio.empty(_eur("10000"))
        proposal = _srd_order(instrument=_AC, qty=Decimal(100))
        prices = {_AC.id: Decimal(50)}
        spy = _SpySafety()
        result = srd_coverage_gate(
            portfolio, proposal, prices, safety=spy
        )
        assert isinstance(result, Ok)
        assert spy.triggers == []
    finally:
        set_srd_eligible_instruments(())


def test_srd_coverage_formula_includes_held_equity_at_haircut():
    """REQ_SDD_SRD_005 — held cash-equity positions contribute
    `EQUITY_COVERAGE_HAIRCUT` × marked-value to coverage."""
    set_srd_eligible_instruments({_AC.id})
    try:
        # Portfolio with €500 cash + a 100-share AC.PA cash position
        # marked at €40 ⇒ equity contribution 0.50 × 4000 = 2000;
        # coverage = 500 + 2000 = 2500. Proposed SRD 100 × €40 = 4000.
        # ratio = 2500/4000 = 0.625 ⇒ Ok (above warn).
        portfolio = Portfolio.empty(_eur("500"))
        # Seed an existing cash position via apply().
        cash_order = Order(
            id=OrderId("ord-cash"),
            instrument=_AC,
            side=Side.BUY,
            quantity=Decimal(100),
            type=OrderType.MARKET,
            stop_loss=_stop(),
            created_at=_T0,
            source_strategy=StrategyId("test"),
        )
        # Manually inject the position (avoid running the full apply
        # path with its bucket + tax wiring for this test fixture).
        from trading_system.models.phase import AllocationBucket
        from trading_system.models.trading import Position

        portfolio._positions[_AC.id] = Position(  # type: ignore[attr-defined]
            instrument=_AC,
            quantity=Decimal(100),
            avg_price=Decimal(40),
            stop_loss=cash_order.stop_loss,
            opened_at=_T0,
        )
        portfolio._position_buckets[_AC.id] = AllocationBucket.STOCK  # type: ignore[attr-defined]
        portfolio._last_prices[_AC.id] = Decimal(40)  # type: ignore[attr-defined]

        proposal = _srd_order(instrument=_AC, qty=Decimal(100))
        prices = {_AC.id: Decimal(40)}
        result = srd_coverage_gate(
            portfolio, proposal, prices, safety=None
        )
        assert isinstance(result, Ok)
    finally:
        set_srd_eligible_instruments(())


# ---------------------------------------------------------------------------
# Defaults sanity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TC_SRD_011 — phase-cap accounting against SRD notional
# ---------------------------------------------------------------------------


def test_exposure_pct_including_srd_counts_srd_notional_for_stock_bucket():
    """REQ_F_SRD_008 / REQ_SDD_SRD_009 — SRD positions contribute
    to the STOCK bucket's exposure at FULL NOTIONAL (not cash
    deployed). A 5:1 levered SRD position counts at €5 000 even
    though no cash was deployed at open."""
    from trading_system.models.phase import AllocationBucket

    set_srd_eligible_instruments({_AC.id})
    try:
        # Portfolio: €10 000 cash; 1 SRD position @ €50 × 100 ⇒
        # notional €5 000. equity_after_tax = €10 000 (no
        # unrealized PnL since marked at entry price).
        portfolio = Portfolio.empty(_eur("10000"))
        order = _srd_order(instrument=_AC, qty=Decimal(100))
        trade = _fill(qty=Decimal(100), price=Decimal(50))
        portfolio.apply_srd_open(order, trade)
        portfolio.mark({_AC.id: Decimal(50)})

        # exposure_pct(STOCK) — without SRD inclusion — is 0
        # because no cash position exists.
        assert portfolio.exposure_pct(AllocationBucket.STOCK) == Decimal(0)
        # exposure_pct_including_srd(STOCK) = SRD notional / equity =
        # 5 000 / 10 000 = 0.50.
        assert (
            portfolio.exposure_pct_including_srd(AllocationBucket.STOCK)
            == Decimal("0.5")
        )
        # Non-STOCK buckets unchanged (always 0 here).
        assert (
            portfolio.exposure_pct_including_srd(AllocationBucket.TURBO)
            == Decimal(0)
        )
    finally:
        set_srd_eligible_instruments(())


def test_exposure_pct_including_srd_falls_back_when_no_srd_positions():
    """REQ_SDD_SRD_009 — when no SRD positions exist, the
    function returns the same value as ``exposure_pct``."""
    from trading_system.models.phase import AllocationBucket

    portfolio = Portfolio.empty(_eur("10000"))
    assert (
        portfolio.exposure_pct_including_srd(AllocationBucket.STOCK)
        == portfolio.exposure_pct(AllocationBucket.STOCK)
    )


def test_default_thresholds_match_documented_values():
    """REQ_F_SRD_004 — defaults pinned at 25%/30% with 50%
    equity-haircut."""
    assert DEFAULT_MIN_COVERAGE_RATIO == Decimal("0.25")
    assert DEFAULT_WARN_COVERAGE_RATIO == Decimal("0.30")
    assert EQUITY_COVERAGE_HAIRCUT == Decimal("0.50")
