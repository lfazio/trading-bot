"""Tests for ``trading_system.dashboard.engine``.

Covers TC_LIF_002 (Dashboard exposes no trade-execution actions —
REQ_F_DSH_001, REQ_SDS_MOD_015) and the field-coverage check for
the rendered DashboardView (REQ_F_DSH_001).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.analytics import Analytics
from trading_system.capital_flow import CapitalFlow
from trading_system.dashboard import Dashboard, DashboardView
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket, Phase
from trading_system.models.trading import Order, OrderType, Side, StopLoss, Trade
from trading_system.portfolio import Portfolio
from trading_system.tax.config import TaxConfig

EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(year: int = 2026, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _stock() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _build_dashboard() -> tuple[Dashboard, Trade]:
    s = _stock()
    p = Portfolio.empty(_eur("10000"))
    cf = CapitalFlow(initial=_eur("10000"))
    o = Order(
        id=OrderId("O1"),
        instrument=s,
        side=Side.BUY,
        quantity=Decimal("10"),
        type=OrderType.MARKET,
        stop_loss=StopLoss(price=Decimal("40")),
        created_at=_ts(),
        source_strategy=StrategyId("core_v1"),
    )
    t = Trade(
        id=TradeId("T1"),
        order_id=o.id,
        executed_at=_ts(),
        price=Decimal("50"),
        quantity_filled=Decimal("10"),
        fees=_eur("1.00"),
    )
    p.apply(t, o, AllocationBucket.STOCK, TaxConfig.default())
    p.record_equity(_ts(2026, 1, 2))
    a = Analytics(portfolio=p, capital_flow=cf, trades=(t,))
    d = Dashboard(analytics=a, phase=Phase.TWO, orders={o.id: o.source_strategy})
    return d, t


# ---------------------------------------------------------------------------
# TC_LIF_002 — REQ_SDS_MOD_015: dashboard has no trade-execution methods
# ---------------------------------------------------------------------------


_FORBIDDEN_METHOD_NAMES = (
    "submit",
    "cancel",
    "place_order",
    "send_order",
    "execute",
    "trade",
    "open_position",
    "close_position",
    "buy",
    "sell",
)


def test_no_trade_actions_on_dashboard_class() -> None:
    public = [
        name
        for name, _ in inspect.getmembers(Dashboard, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    for forbidden in _FORBIDDEN_METHOD_NAMES:
        assert forbidden not in public, (
            f"Dashboard exposes forbidden trade-execution method {forbidden!r} (REQ_SDS_MOD_015)"
        )


def test_dashboard_public_surface_is_render_only() -> None:
    public = sorted(
        name
        for name, _ in inspect.getmembers(Dashboard, predicate=inspect.isfunction)
        if not name.startswith("_")
    )
    assert public == ["render"], f"Dashboard public surface drifted from render-only: {public}"


# ---------------------------------------------------------------------------
# REQ_F_DSH_001 — required fields populated
# ---------------------------------------------------------------------------


class TestRender:
    def test_returns_dashboard_view(self) -> None:
        d, _ = _build_dashboard()
        view = d.render(_ts(2026, 2))
        assert isinstance(view, DashboardView)

    def test_view_carries_required_fields(self) -> None:
        d, t = _build_dashboard()
        view = d.render(_ts(2026, 2))
        assert view.phase is Phase.TWO
        assert view.allocation  # at least one row
        assert view.turbo_exposure_pct == Decimal(0)
        assert view.performance.trade_count == 1
        assert len(view.trade_history) == 1
        assert view.trade_history[0].trade_id == t.id
        assert view.trade_history[0].strategy == StrategyId("core_v1")
        # Attribution always emits at least the NAV row.
        assert view.attribution[0].kind == "nav"

    def test_history_limit_truncates(self) -> None:
        d, _ = _build_dashboard()
        with pytest.raises(ValueError, match="history_limit"):
            d.render(_ts(), history_limit=0)
        # Limit 1; we only have 1 trade so 1 should pass.
        view = d.render(_ts(), history_limit=1)
        assert len(view.trade_history) == 1
