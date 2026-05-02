"""Tests for ``trading_system.execution.types`` (Tick, Account)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.execution.types import Account, Tick
from trading_system.models.identifiers import InstrumentId
from trading_system.models.money import Currency, Money

EUR = Currency.EUR
USD = Currency.USD


def make_tick(**overrides: object) -> Tick:
    base: dict[str, object] = {
        "at": datetime(2026, 5, 1, 10, 0),
        "instrument_id": InstrumentId("ABC"),
        "bid": Decimal("100.00"),
        "ask": Decimal("100.10"),
        "last": Decimal("100.05"),
    }
    base.update(overrides)
    return Tick(**base)  # type: ignore[arg-type]


class TestTick:
    def test_valid(self) -> None:
        t = make_tick()
        assert t.bid == Decimal("100.00")

    @pytest.mark.parametrize("field", ["bid", "ask", "last"])
    def test_non_positive_price_rejected(self, field: str) -> None:
        with pytest.raises(ValueError, match=rf"Tick\.{field} must be > 0"):
            make_tick(**{field: Decimal(0)})

    def test_bid_above_ask_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"Tick\.bid.*<= ask"):
            make_tick(bid=Decimal("101"), ask=Decimal("100"))

    def test_last_outside_band_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"Tick\.last.*lie in"):
            make_tick(last=Decimal("99.50"))
        with pytest.raises(ValueError, match=r"Tick\.last.*lie in"):
            make_tick(last=Decimal("100.50"))

    def test_negative_volume_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"Tick\.volume must be >= 0"):
            make_tick(volume=Decimal(-1))


class TestAccount:
    def test_valid(self) -> None:
        a = Account(
            cash=Money(Decimal(1000), EUR),
            realized_pnl=Money(Decimal(50), EUR),
            unrealized_pnl=Money(Decimal(20), EUR),
            equity=Money(Decimal(1070), EUR),
        )
        assert a.equity.amount == Decimal(1070)

    def test_currency_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="must share a currency"):
            Account(
                cash=Money(Decimal(1000), EUR),
                realized_pnl=Money(Decimal(50), USD),
                unrealized_pnl=Money(Decimal(20), EUR),
                equity=Money(Decimal(1070), EUR),
            )
