"""CR-030 / TC_SRD_001..003 — SRDPosition + Order.SRD_* eligibility.

REQ refs:
- REQ_F_SRD_001 — srd-eligible.yaml loads via UniverseLoader.
- REQ_F_SRD_002 — Order.SRD_* validates eligibility at construction.
- REQ_F_SRD_003 — SRDPosition invariants.
- REQ_SDD_SRD_001..003 — design contracts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from trading_system.data.universes import load_universe
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
)
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.models import trading as _trading
from trading_system.models.trading import (
    Order,
    OrderType,
    Side,
    StopLoss,
    set_srd_eligible_instruments,
)
from trading_system.portfolio.srd_position import (
    SRDPosition,
    last_business_day_of_month,
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


def _stop() -> StopLoss:
    return StopLoss(price=Decimal("90"))


# ---------------------------------------------------------------------------
# TC_SRD_001 — srd-eligible.yaml loads + frozenset shape
# ---------------------------------------------------------------------------


def test_srd_eligible_yaml_loads_with_canonical_universe_shape():
    """REQ_F_SRD_001 / REQ_SDD_SRD_001 — the YAML loads via the
    shared UniverseLoader + carries the standard Stock shape."""
    res = load_universe("srd-eligible")
    assert res.is_ok(), f"unexpected Err: {res}"
    universe = res.unwrap()
    assert len(universe.stocks) >= 28  # starter list ≥ 28 names
    symbols = {s.symbol for s in universe.stocks}
    # CAC 40 heavyweights MUST be present in the SRD-eligible list.
    assert {"AC", "AI", "AIR", "BNP", "MC", "TTE"} <= symbols


def test_set_srd_eligible_instruments_populates_frozenset():
    """REQ_SDD_SRD_001 — set_srd_eligible_instruments replaces the
    module-level frozenset. Idempotent + deterministic."""
    set_srd_eligible_instruments({_AC.id})
    assert _AC.id in _trading.SRD_ELIGIBLE_INSTRUMENT_IDS
    assert _NON_ELIGIBLE.id not in _trading.SRD_ELIGIBLE_INSTRUMENT_IDS
    # Clean up so other tests aren't affected.
    set_srd_eligible_instruments(())
    assert _trading.SRD_ELIGIBLE_INSTRUMENT_IDS == frozenset()


# ---------------------------------------------------------------------------
# TC_SRD_002 — Order.SRD_* eligibility check
# ---------------------------------------------------------------------------


def test_order_srd_long_against_eligible_instrument_constructs():
    """REQ_F_SRD_002 — SRD_LONG on an eligible instrument passes."""
    set_srd_eligible_instruments({_AC.id})
    try:
        order = Order(
            id=OrderId("ord-1"),
            instrument=_AC,
            side=Side.BUY,
            quantity=Decimal(10),
            type=OrderType.SRD_LONG,
            stop_loss=_stop(),
            created_at=_T0,
            source_strategy=StrategyId("test"),
        )
        assert order.type is OrderType.SRD_LONG
    finally:
        set_srd_eligible_instruments(())


@pytest.mark.parametrize(
    "order_type",
    [OrderType.SRD_LONG, OrderType.SRD_SHORT],
)
def test_order_srd_against_non_eligible_instrument_raises(order_type):
    """REQ_F_SRD_002 / REQ_SDD_SRD_002 — SRD_* against a
    non-eligible instrument raises ValueError at construction."""
    set_srd_eligible_instruments({_AC.id})  # AC eligible, ASML not
    try:
        with pytest.raises(ValueError, match="SRD-eligible instrument"):
            Order(
                id=OrderId("ord-1"),
                instrument=_NON_ELIGIBLE,
                side=Side.BUY,
                quantity=Decimal(10),
                type=order_type,
                stop_loss=_stop(),
                created_at=_T0,
                source_strategy=StrategyId("test"),
            )
    finally:
        set_srd_eligible_instruments(())


def test_order_market_against_non_eligible_instrument_still_constructs():
    """REQ_F_SRD_002 — eligibility check is SRD-only. A cash-equity
    MARKET order against the same non-eligible instrument SHALL
    construct successfully."""
    set_srd_eligible_instruments({_AC.id})  # ASML NOT eligible
    try:
        order = Order(
            id=OrderId("ord-1"),
            instrument=_NON_ELIGIBLE,
            side=Side.BUY,
            quantity=Decimal(10),
            type=OrderType.MARKET,
            stop_loss=_stop(),
            created_at=_T0,
            source_strategy=StrategyId("test"),
        )
        assert order.type is OrderType.MARKET
    finally:
        set_srd_eligible_instruments(())


# ---------------------------------------------------------------------------
# TC_SRD_003 — SRDPosition invariants + last-business-day helper
# ---------------------------------------------------------------------------


def test_srd_position_constructs_with_documented_defaults():
    """REQ_F_SRD_003 — happy-path construction."""
    pos = SRDPosition(
        instrument=_AC,
        direction="LONG",
        quantity=Decimal(10),
        entry_price=Decimal(50),
        entry_at=_T0,
        settlement_cycle=last_business_day_of_month(_T0),
    )
    assert pos.direction == "LONG"
    assert pos.carry_fee_rate_monthly == Decimal("0.0025")
    assert pos.auto_rollover is False


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"direction": "INVALID"}, "direction must be"),
        ({"quantity": Decimal(0)}, "quantity must be > 0"),
        ({"quantity": Decimal(-1)}, "quantity must be > 0"),
        ({"entry_price": Decimal(0)}, "entry_price must be > 0"),
        ({"entry_price": Decimal(-1)}, "entry_price must be > 0"),
        ({"carry_fee_rate_monthly": Decimal("-0.01")}, "carry_fee_rate_monthly"),
    ],
)
def test_srd_position_rejects_invalid_inputs(kwargs, match):
    """REQ_SDD_SRD_003 — __post_init__ invariants."""
    base = {
        "instrument": _AC,
        "direction": "LONG",
        "quantity": Decimal(10),
        "entry_price": Decimal(50),
        "entry_at": _T0,
        "settlement_cycle": last_business_day_of_month(_T0),
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        SRDPosition(**base)


def test_last_business_day_of_month_walks_back_from_weekend():
    """REQ_SDD_SRD_003 — May 2026 ends on Sunday May 31; the
    helper SHALL walk back to Friday May 29."""
    at = datetime(2026, 5, 15, tzinfo=UTC)
    last = last_business_day_of_month(at)
    assert last.date() == date(2026, 5, 29)
    assert last.tzinfo is UTC


def test_last_business_day_of_month_honours_holidays():
    """REQ_SDD_SRD_003 — operator-supplied holidays roll back to the
    preceding business day."""
    at = datetime(2026, 12, 15, tzinfo=UTC)
    # Dec 31 2026 is a Thursday; mark it as a holiday + Dec 30
    # (Wednesday) too — expect Dec 29 (Tuesday).
    holidays = frozenset({date(2026, 12, 31), date(2026, 12, 30)})
    last = last_business_day_of_month(at, holidays=holidays)
    assert last.date() == date(2026, 12, 29)
