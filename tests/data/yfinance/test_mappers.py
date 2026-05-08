"""Tests for ``trading_system.data.yfinance.mappers``.

Covers TC_DAT_008 (Decimal-only at the boundary), TC_DAT_009
(Bar.close = unadjusted), TC_DAT_010 (per-share dividends).

REQ refs: REQ_F_DAT_007, REQ_F_DAT_008, REQ_F_DAT_003,
REQ_SDS_DAT_003.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trading_system.data.yfinance.mappers import bars_from_yf, dividends_from_yf
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money

EUR = Currency.EUR


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


# ----------------------------------------------------------------------
# Tiny duck-typed stand-ins for pandas DataFrame / Series
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Row:
    # Mirrors pandas DataFrame.itertuples row attribute names — must
    # be CapWords for the mapper's row.Index / row.Open / ... access
    # pattern to work, even though that violates PEP 8.
    Index: Any
    Open: Any
    High: Any
    Low: Any
    Close: Any
    Volume: Any


@dataclass(slots=True)
class _DF:
    rows: list[_Row]

    def itertuples(self) -> list[_Row]:
        return self.rows


# ----------------------------------------------------------------------
# bars_from_yf
# ----------------------------------------------------------------------


class TestBarsFromYf:
    def test_decimal_only_at_boundary(self) -> None:
        # All-float input — output Bar fields must be Decimal.
        df = _DF(
            rows=[
                _Row(
                    Index=datetime(2026, 1, 2, tzinfo=UTC),
                    Open=100.5,
                    High=101.25,
                    Low=99.75,
                    Close=100.875,
                    Volume=12345,
                ),
            ]
        )
        bars = bars_from_yf(df, _stock())
        assert len(bars) == 1
        b = bars[0]
        assert isinstance(b.open, Decimal)
        assert isinstance(b.high, Decimal)
        assert isinstance(b.low, Decimal)
        assert isinstance(b.close, Decimal)
        assert isinstance(b.volume, Decimal)
        # Decimal(str(100.5)) = Decimal("100.5") — no float repr noise.
        assert b.open == Decimal("100.5")
        assert b.close == Decimal("100.875")

    def test_bar_close_is_unadjusted(self) -> None:
        # The mapper takes ``Close`` (raw) — there's no Adj Close
        # column, so split / dividend adjustments live in the
        # dividend stream, not in Bar.close (REQ_F_DAT_008).
        df = _DF(
            rows=[
                _Row(
                    Index=datetime(2026, 1, 2, tzinfo=UTC),
                    Open=Decimal("100"),
                    High=Decimal("101"),
                    Low=Decimal("99"),
                    Close=Decimal("100.5"),
                    Volume=1000,
                ),
            ]
        )
        bars = bars_from_yf(df, _stock())
        assert bars[0].close == Decimal("100.5")

    def test_decimal_input_preserved(self) -> None:
        df = _DF(
            rows=[
                _Row(
                    Index=datetime(2026, 1, 2, tzinfo=UTC),
                    Open=Decimal("100.5"),
                    High=Decimal("101"),
                    Low=Decimal("100"),
                    Close=Decimal("100.5"),
                    Volume=Decimal("1000"),
                ),
            ]
        )
        bars = bars_from_yf(df, _stock())
        assert bars[0].open == Decimal("100.5")

    def test_pandas_timestamp_unwrapped(self) -> None:
        # If the Index has .to_pydatetime() it should be called.
        class _Ts:
            def __init__(self, dt: datetime) -> None:
                self._dt = dt

            def to_pydatetime(self) -> datetime:
                return self._dt

        target = datetime(2026, 1, 3, tzinfo=UTC)
        df = _DF(
            rows=[
                _Row(
                    Index=_Ts(target),
                    Open=Decimal("100"),
                    High=Decimal("101"),
                    Low=Decimal("99"),
                    Close=Decimal("100"),
                    Volume=Decimal("0"),
                ),
            ]
        )
        bars = bars_from_yf(df, _stock())
        assert bars[0].at == target


# ----------------------------------------------------------------------
# dividends_from_yf
# ----------------------------------------------------------------------


class TestDividendsFromYf:
    def test_per_share_amount_carries_through_as_decimal(self) -> None:
        ts = datetime(2026, 6, 15, tzinfo=UTC)
        divs = dividends_from_yf([(ts, 0.5)], _stock(), EUR)
        assert len(divs) == 1
        d = divs[0]
        # amount_gross is per-share; simulator multiplies later.
        assert d.amount_gross == Money(Decimal("0.5"), EUR)
        assert d.ex_date == ts
        assert d.pay_date == ts

    def test_decimal_input_preserved(self) -> None:
        ts = datetime(2026, 6, 15, tzinfo=UTC)
        divs = dividends_from_yf([(ts, Decimal("0.50"))], _stock(), EUR)
        assert divs[0].amount_gross.amount == Decimal("0.50")
