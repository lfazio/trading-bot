"""Tests for ``trading_system.data.yfinance.symbols``.

Covers TC_DAT_012 (Yahoo-suffix mapping for AS / PA / DE / L; bare
symbol for US listings; Err for unknown exchange).

REQ refs: REQ_F_DAT_002, REQ_SDD_DAT_011, REQ_SDD_DAT_012.
"""

from __future__ import annotations

import pytest

from trading_system.data.yfinance.symbols import yahoo_symbol_for
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err, Ok


def _stock(symbol: str, exchange: str) -> Stock:
    return Stock(
        id=InstrumentId(f"{symbol}.{exchange}"),
        symbol=symbol,
        exchange=exchange,
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin=f"FR000{symbol}",
        sector="x",
        country="x",
    )


@pytest.mark.parametrize(
    ("exchange", "expected_suffix"),
    [
        ("AS", "AS"),
        ("PA", "PA"),
        ("BR", "BR"),
        ("DE", "DE"),
        ("F", "F"),
        ("L", "L"),
        ("SW", "SW"),
    ],
)
def test_eu_exchanges_get_suffix(exchange: str, expected_suffix: str) -> None:
    res = yahoo_symbol_for(_stock("ABC", exchange))
    match res:
        case Ok(sym):
            assert sym == f"ABC.{expected_suffix}"
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")


@pytest.mark.parametrize("exchange", ["NYSE", "NASDAQ", "AMEX", "ARCA", "US"])
def test_us_exchanges_get_bare_symbol(exchange: str) -> None:
    res = yahoo_symbol_for(_stock("AAPL", exchange))
    match res:
        case Ok(sym):
            assert sym == "AAPL"
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")


def test_unknown_exchange_returns_err() -> None:
    res = yahoo_symbol_for(_stock("XYZ", "MOON"))
    match res:
        case Ok(sym):
            raise AssertionError(f"expected Err, got Ok({sym!r})")
        case Err(reason):
            assert reason == "data:unknown_exchange:MOON"
