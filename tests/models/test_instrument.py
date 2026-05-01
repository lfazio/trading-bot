"""Tests for ``trading_system.models.instrument``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import (
    Instrument,
    InstrumentClass,
    Stock,
    StructuredProduct,
    Turbo,
)
from trading_system.models.money import Currency, Money

EUR = Currency.EUR


def make_base(symbol: str = "ABC", cls: InstrumentClass = InstrumentClass.STOCK) -> Instrument:
    return Instrument(
        id=InstrumentId(f"id-{symbol}"),
        symbol=symbol,
        exchange="EPA",
        currency=EUR,
        cls=cls,
    )


class TestInstrumentBase:
    def test_basic_construction(self) -> None:
        i = make_base()
        assert i.symbol == "ABC"
        assert i.cls is InstrumentClass.STOCK

    def test_empty_symbol_rejected(self) -> None:
        with pytest.raises(ValueError, match="symbol must be non-empty"):
            Instrument(InstrumentId("x"), "", "EPA", EUR, InstrumentClass.STOCK)

    def test_empty_exchange_rejected(self) -> None:
        with pytest.raises(ValueError, match="exchange must be non-empty"):
            Instrument(InstrumentId("x"), "ABC", "", EUR, InstrumentClass.STOCK)


class TestStock:
    def test_valid(self) -> None:
        s = Stock(
            id=InstrumentId("aapl"),
            symbol="AAPL",
            exchange="NSQ",
            currency=EUR,
            cls=InstrumentClass.STOCK,
            isin="US0378331005",
            sector="Tech",
            country="US",
        )
        assert s.isin == "US0378331005"

    def test_wrong_class_rejected(self) -> None:
        with pytest.raises(ValueError, match="cls must be STOCK"):
            Stock(
                id=InstrumentId("x"),
                symbol="ABC",
                exchange="EPA",
                currency=EUR,
                cls=InstrumentClass.TURBO,
                isin="ISIN",
                sector="Tech",
                country="US",
            )

    def test_empty_isin_rejected(self) -> None:
        with pytest.raises(ValueError, match="isin must be non-empty"):
            Stock(
                id=InstrumentId("x"),
                symbol="ABC",
                exchange="EPA",
                currency=EUR,
                cls=InstrumentClass.STOCK,
                isin="",
                sector="Tech",
                country="US",
            )


def make_turbo(**overrides: object) -> Turbo:
    base = {
        "id": InstrumentId("t-1"),
        "symbol": "T1",
        "exchange": "EPA",
        "currency": EUR,
        "cls": InstrumentClass.TURBO,
        "underlying": InstrumentId("AAPL"),
        "direction": "LONG",
        "leverage": Decimal("3"),
        "knockout": Decimal("90"),
        "spread_pct": Decimal("0.005"),
    }
    base.update(overrides)
    return Turbo(**base)  # type: ignore[arg-type]


class TestTurbo:
    def test_valid(self) -> None:
        t = make_turbo()
        assert t.leverage == Decimal("3")
        assert t.direction == "LONG"

    def test_wrong_class_rejected(self) -> None:
        with pytest.raises(ValueError, match="cls must be TURBO"):
            make_turbo(cls=InstrumentClass.STOCK)

    def test_empty_underlying_rejected(self) -> None:
        with pytest.raises(ValueError, match="underlying must be non-empty"):
            make_turbo(underlying=InstrumentId(""))

    def test_invalid_direction_rejected(self) -> None:
        with pytest.raises(ValueError, match="direction must be LONG or SHORT"):
            make_turbo(direction="UP")  # type: ignore[arg-type]

    def test_leverage_must_exceed_1(self) -> None:
        with pytest.raises(ValueError, match="leverage must be > 1"):
            make_turbo(leverage=Decimal("1"))

    def test_knockout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="knockout must be > 0"):
            make_turbo(knockout=Decimal(0))

    def test_negative_spread_rejected(self) -> None:
        with pytest.raises(ValueError, match="spread_pct must be >= 0"):
            make_turbo(spread_pct=Decimal("-0.01"))


def make_sp(**overrides: object) -> StructuredProduct:
    base = {
        "id": InstrumentId("sp-1"),
        "symbol": "SP1",
        "exchange": "EPA",
        "currency": EUR,
        "cls": InstrumentClass.STRUCTURED,
        "underlying": InstrumentId("CAC40"),
        "payoff": "AUTOCALL",
        "issuer": "BNP",
        "barriers": (Decimal("0.7"),),
        "notional": Money(Decimal(1000), EUR),
    }
    base.update(overrides)
    return StructuredProduct(**base)  # type: ignore[arg-type]


class TestStructuredProduct:
    def test_valid(self) -> None:
        p = make_sp()
        assert p.issuer == "BNP"
        assert p.payoff == "AUTOCALL"

    def test_wrong_class_rejected(self) -> None:
        with pytest.raises(ValueError, match="cls must be STRUCTURED"):
            make_sp(cls=InstrumentClass.STOCK)

    def test_empty_underlying_rejected(self) -> None:
        with pytest.raises(ValueError, match="underlying must be non-empty"):
            make_sp(underlying=InstrumentId(""))

    def test_invalid_payoff_rejected(self) -> None:
        with pytest.raises(ValueError, match="payoff invalid"):
            make_sp(payoff="UNKNOWN")  # type: ignore[arg-type]

    def test_empty_issuer_rejected(self) -> None:
        with pytest.raises(ValueError, match="issuer must be non-empty"):
            make_sp(issuer="")

    def test_zero_notional_rejected(self) -> None:
        with pytest.raises(ValueError, match="notional must be > 0"):
            make_sp(notional=Money(Decimal(0), EUR))
