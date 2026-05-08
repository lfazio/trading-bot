"""Pure converters from yfinance result shapes to our domain types.

The mappers are *duck-typed* on the input: any object that exposes
``.itertuples()`` yielding rows with ``Index, Open, High, Low,
Close, Volume`` attributes is accepted for the bars converter, and
any iterable of ``(timestamp, amount)`` pairs is accepted for the
dividends converter. This keeps ``pandas`` and ``yfinance`` out of
the test path — fixtures and unit tests pass tiny stubs.

Decimal-only at the boundary (REQ_F_DAT_007 / REQ_SDS_DAT_003):
every numeric field is converted via ``Decimal(str(...))`` so float
representation noise never reaches downstream Bar / Dividend types.
``Bar.close`` carries the **unadjusted** close (REQ_F_DAT_008);
splits and dividends are surfaced via the dividend stream.

REQ refs: REQ_F_DAT_007, REQ_F_DAT_008, REQ_F_DAT_003,
REQ_SDS_DAT_003.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from trading_system.data.types import Bar
from trading_system.models.instrument import Instrument
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Dividend


def bars_from_yf(
    df: Any,
    instrument: Instrument,  # reserved for future per-instrument adjustment
) -> list[Bar]:
    """Convert a yfinance-style OHLCV table to ``list[Bar]``.

    Row attributes: ``Index`` (timestamp; if it has ``.to_pydatetime()``
    we call it, otherwise it must already be a ``datetime``),
    ``Open``, ``High``, ``Low``, ``Close``, ``Volume``.
    """
    _ = instrument  # silence unused-arg lint; reserved.
    out: list[Bar] = []
    for row in df.itertuples():
        idx = row.Index
        at = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        out.append(
            Bar(
                at=at,
                open=_to_decimal(row.Open),
                high=_to_decimal(row.High),
                low=_to_decimal(row.Low),
                close=_to_decimal(row.Close),
                volume=_to_decimal(row.Volume),
            )
        )
    return out


def dividends_from_yf(
    series: Iterable[tuple[datetime, Any]],
    instrument: Instrument,
    currency: Currency,
) -> list[Dividend]:
    """Convert a yfinance dividend series to ``list[Dividend]``.

    Yahoo records collapse ex-date and pay-date onto a single
    timestamp; we mirror that on the ``Dividend`` object. Amounts are
    **per-share** — the ``DividendSimulator`` multiplies by holdings
    at apply time (REQ_F_DAT_003 / SDD §6.3 contract).
    """
    out: list[Dividend] = []
    for ts, amount in series:
        at = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        out.append(
            Dividend(
                instrument=instrument.id,
                ex_date=at,
                pay_date=at,
                amount_gross=Money(_to_decimal(amount), currency),
            )
        )
    return out


def _to_decimal(value: Any) -> Decimal:
    """Convert a numeric (typically ``float`` from pandas) to ``Decimal``
    by way of ``str()`` — avoids float repr noise that direct
    ``Decimal(value)`` construction would carry through."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
