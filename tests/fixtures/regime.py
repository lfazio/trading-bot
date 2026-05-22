"""Synthetic regime fixtures — REQ_TP_FIX_003.

REQ_TP_FIX_003 — Synthetic regime fixtures (BULL / BEAR /
SIDEWAYS / HIGH_VOL) SHALL be reused across screener, strategies,
backtesting, structured products, and meta-loop tests so regime
semantics stay consistent across modules.

Each fixture returns a ``tuple[Bar, ...]`` with deterministic OHLCV
shapes that match the regime label. The bars are pure Decimal
arithmetic — no RNG, no I/O — so two callers see byte-identical
sequences.

Usage:

    from tests.fixtures.regime import bull_bars, sideways_bars
    bars = bull_bars(days=30)

The synthetic shape isn't trying to fool a sophisticated regime
detector; it's a small, predictable input for unit tests that
need a regime-labeled bar sequence. Consumers SHOULD pass the
fixture's output through their own regime-aware code; the fixture's
contract is "this bar sequence, viewed as a whole, embodies the
named regime".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.data.types import Bar


_BASE_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_BASE_PRICE = Decimal("100.00")
_DEFAULT_VOLUME = Decimal("1000")


def _make_bar(
    *, at: datetime, open_: Decimal, high: Decimal, low: Decimal, close: Decimal
) -> Bar:
    return Bar(
        at=at,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=_DEFAULT_VOLUME,
    )


def bull_bars(days: int = 30) -> tuple[Bar, ...]:
    """REQ_TP_FIX_003 — synthetic BULL regime.

    Returns ``days`` daily bars whose close grows by 0.5 % per
    step from ``_BASE_PRICE``. Each bar's high/low straddle the
    open/close by a small fixed margin so the OHLC invariants
    hold.
    """
    out: list[Bar] = []
    close = _BASE_PRICE
    for i in range(days):
        open_ = close
        close = (open_ * Decimal("1.005")).quantize(Decimal("0.01"))
        high = (close * Decimal("1.002")).quantize(Decimal("0.01"))
        low = (open_ * Decimal("0.998")).quantize(Decimal("0.01"))
        out.append(
            _make_bar(
                at=_BASE_AT + timedelta(days=i),
                open_=open_,
                high=high,
                low=low,
                close=close,
            )
        )
    return tuple(out)


def bear_bars(days: int = 30) -> tuple[Bar, ...]:
    """REQ_TP_FIX_003 — synthetic BEAR regime.

    Mirror of ``bull_bars``: close shrinks by 0.5 % per step.
    """
    out: list[Bar] = []
    close = _BASE_PRICE
    for i in range(days):
        open_ = close
        close = (open_ * Decimal("0.995")).quantize(Decimal("0.01"))
        high = (open_ * Decimal("1.002")).quantize(Decimal("0.01"))
        low = (close * Decimal("0.998")).quantize(Decimal("0.01"))
        out.append(
            _make_bar(
                at=_BASE_AT + timedelta(days=i),
                open_=open_,
                high=high,
                low=low,
                close=close,
            )
        )
    return tuple(out)


def sideways_bars(days: int = 30) -> tuple[Bar, ...]:
    """REQ_TP_FIX_003 — synthetic SIDEWAYS regime.

    Close oscillates around ``_BASE_PRICE`` with tiny ± 0.2 %
    moves; net drift over ``days`` is zero.
    """
    out: list[Bar] = []
    close = _BASE_PRICE
    for i in range(days):
        open_ = close
        # Alternate up/down by 0.2 % so the series returns to base.
        sign = Decimal("1.002") if i % 2 == 0 else Decimal("0.998")
        close = (open_ * sign).quantize(Decimal("0.01"))
        high = max(open_, close)
        low = min(open_, close)
        out.append(
            _make_bar(
                at=_BASE_AT + timedelta(days=i),
                open_=open_,
                high=high,
                low=low,
                close=close,
            )
        )
    return tuple(out)


def high_vol_bars(days: int = 30) -> tuple[Bar, ...]:
    """REQ_TP_FIX_003 — synthetic HIGH_VOL regime.

    Close alternates by ± 5 % per step — much larger moves than
    the bull / bear regimes — so any volatility-window detector
    classifies the series as HIGH_VOL.
    """
    out: list[Bar] = []
    close = _BASE_PRICE
    for i in range(days):
        open_ = close
        sign = Decimal("1.05") if i % 2 == 0 else Decimal("0.95")
        close = (open_ * sign).quantize(Decimal("0.01"))
        high = (max(open_, close) * Decimal("1.01")).quantize(Decimal("0.01"))
        low = (min(open_, close) * Decimal("0.99")).quantize(Decimal("0.01"))
        out.append(
            _make_bar(
                at=_BASE_AT + timedelta(days=i),
                open_=open_,
                high=high,
                low=low,
                close=close,
            )
        )
    return tuple(out)


# Closed set — the four regimes named in REQ_F_MTO_008. A
# conformance test asserts every member is callable and produces
# a non-empty sequence on the default day count.
REGIME_FIXTURES = {
    "bull": bull_bars,
    "bear": bear_bars,
    "sideways": sideways_bars,
    "high_vol": high_vol_bars,
}
