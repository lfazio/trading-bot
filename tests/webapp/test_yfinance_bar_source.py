"""Tests for ``YFinanceBarSource`` (REQ_F_PAP_002).

Pure-function tests on a fake ``MarketDataProvider`` so the
test stays independent of the real yfinance adapter + network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.data.types import Bar, Timeframe
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.webapp.runtimes.yfinance_bar_source import (
    YFinanceBarSource,
)


_T0 = datetime(2026, 5, 23, 9, 0, tzinfo=UTC)


def _stock() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _bar(*, at: datetime, close: str) -> Bar:
    p = Decimal(close)
    return Bar(
        at=at,
        open=p,
        high=p * Decimal("1.001"),
        low=p * Decimal("0.999"),
        close=p,
        volume=Decimal("1000"),
    )


@dataclass(slots=True)
class _FakeProvider:
    """In-memory ``MarketDataProvider`` stub."""

    bar_response: list = field(default_factory=list)
    bar_err: str | None = None
    latest_response: Bar | None = None
    calls: int = 0
    live_response: list | None = None
    live_calls: int = 0

    def bars(self, instrument, timeframe, start, end):
        self.calls += 1
        if self.bar_err is not None:
            return Err(self.bar_err)
        return Ok(self.bar_response)

    def latest(self, instrument):
        if self.latest_response is None:
            return Err("data:not_found:latest")
        return Ok(self.latest_response)

    def dividends(self, instrument, year):
        return Ok([])

    def fundamentals(self, instrument):
        return Err("data:not_supported:fakes")


@dataclass(slots=True)
class _FakeLiveProvider(_FakeProvider):
    """Extends _FakeProvider with the CR-022 ``fetch_live_bars`` hook."""

    def fetch_live_bars(self, instrument, timeframe, start, end):
        self.live_calls += 1
        if self.live_response is None:
            # Default: same response as ``bars`` so single-stub tests
            # still work.
            return self.bars(instrument, timeframe, start, end)
        return Ok(self.live_response)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_next_bar_streams_backfill_in_order() -> None:
    """The first call primes the backfill queue with the full
    window; subsequent calls pop bars in ascending date order so
    the equity curve builds up history instead of jumping to the
    most-recent bar in one tick."""
    provider = _FakeProvider(
        bar_response=[
            _bar(at=_T0, close="100"),
            _bar(at=_T0 + timedelta(days=1), close="105"),
        ]
    )
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    # First call returns the OLDEST bar.
    first = src.next_bar()
    assert isinstance(first, Ok) and isinstance(first.value, Some)
    assert first.value.value.close == Decimal("100")
    # Second call returns the next bar in the queue.
    second = src.next_bar()
    assert isinstance(second, Ok) and isinstance(second.value, Some)
    assert second.value.value.close == Decimal("105")


def test_history_accumulates_across_backfill_drain() -> None:
    """After the backfill drains, ``history()`` SHALL contain
    every surfaced bar so the dashboard sparkline + price chart
    see the full series."""
    provider = _FakeProvider(
        bar_response=[
            _bar(at=_T0, close="100"),
            _bar(at=_T0 + timedelta(days=1), close="101"),
            _bar(at=_T0 + timedelta(days=2), close="102"),
        ]
    )
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    # Drain the queue.
    src.next_bar()
    src.next_bar()
    src.next_bar()
    history = src.history()
    assert len(history) == 3
    assert [b.close for b in history] == [
        Decimal("100"),
        Decimal("101"),
        Decimal("102"),
    ]


def test_next_bar_returns_nothing_when_no_newer_data() -> None:
    """Calling ``next_bar`` again with the same provider response
    SHALL return ``Ok(Nothing)`` since the bar's ``at`` doesn't
    advance — markets haven't ticked since the last poll."""
    provider = _FakeProvider(
        bar_response=[_bar(at=_T0, close="100")]
    )
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    # First call surfaces the bar.
    first = src.next_bar()
    assert isinstance(first, Ok) and isinstance(first.value, Some)
    # Second call SHALL be a no-op until newer data arrives.
    second = src.next_bar()
    assert isinstance(second, Ok)
    assert isinstance(second.value, Nothing)


def test_next_bar_advances_when_provider_emits_newer_bar() -> None:
    provider = _FakeProvider(
        bar_response=[_bar(at=_T0, close="100")]
    )
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    src.next_bar()
    # Simulate the next poll seeing a newer bar.
    provider.bar_response = [
        _bar(at=_T0, close="100"),
        _bar(at=_T0 + timedelta(days=1), close="105"),
    ]
    result = src.next_bar()
    assert isinstance(result, Ok) and isinstance(result.value, Some)
    assert result.value.value.close == Decimal("105")


# ---------------------------------------------------------------------------
# Empty response
# ---------------------------------------------------------------------------


def test_next_bar_returns_nothing_on_empty_provider_response() -> None:
    provider = _FakeProvider(bar_response=[])
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    result = src.next_bar()
    assert isinstance(result, Ok)
    assert isinstance(result.value, Nothing)


# ---------------------------------------------------------------------------
# Graceful degradation (REQ_F_PAP_002)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_err",
    [
        "data:cache_miss_offline:ASML.AS",
        "data:network:curl:7",
        "data:network:yfinance_not_installed",
        # yfinance returns an empty DataFrame when curl couldn't
        # reach the upstream — surfaced by the provider as
        # ``data:not_found:<symbol>``. This was the operator-
        # reported failure mode where the panel spammed
        # "not_found" instead of degrading.
        "data:not_found:ASML.AS",
        "data:rate_limited:yahoo",
        "upstream:proxy_timeout",
    ],
)
def test_upstream_err_categories_map_to_upstream_blocked(raw_err: str) -> None:
    """REQ_F_PAP_002 — network / cache-miss / not-found /
    rate-limited / upstream Errs SHALL all collapse to
    ``data:upstream_blocked`` so the paper runtime falls back
    cleanly instead of surfacing a confusing categorised Err."""
    provider = _FakeProvider(bar_err=raw_err)
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    result = src.next_bar()
    assert isinstance(result, Err)
    assert result.error == "data:upstream_blocked"


def test_unknown_err_category_propagates_unchanged() -> None:
    """Errs the wrapper doesn't recognise SHALL surface as-is so
    the operator + the runtime see the categorised code."""
    provider = _FakeProvider(bar_err="data:invalid_range")
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    result = src.next_bar()
    assert isinstance(result, Err)
    assert result.error == "data:invalid_range"


def test_bar_dataclass_already_rejects_zero_prices() -> None:
    """Defensive layer note: ``Bar.__post_init__`` already
    rejects ``open <= 0`` so the source's own zero-price guard
    is a belt-and-suspenders fallback (only reachable if a
    future provider returns a tampered tuple bypassing the
    dataclass)."""
    with pytest.raises(ValueError, match="Bar.open"):
        Bar(
            at=_T0,
            open=Decimal("0"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )


# ---------------------------------------------------------------------------
# latest_cached fallback
# ---------------------------------------------------------------------------


def test_latest_cached_returns_in_memory_snapshot_when_available() -> None:
    """After at least one successful ``next_bar``, ``latest_cached``
    SHALL return the in-memory snapshot — it does NOT re-poll
    the provider."""
    provider = _FakeProvider(bar_response=[_bar(at=_T0, close="100")])
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    src.next_bar()
    cached = src.latest_cached()
    assert isinstance(cached, Ok) and isinstance(cached.value, Some)
    assert cached.value.value.close == Decimal("100")


def test_latest_cached_falls_back_to_provider_latest() -> None:
    """Before any ``next_bar`` succeeds, ``latest_cached`` SHALL
    consult the provider's own ``latest()`` so the disk cache
    is still authoritative."""
    provider = _FakeProvider(
        latest_response=_bar(at=_T0, close="99")
    )
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    cached = src.latest_cached()
    assert isinstance(cached, Ok) and isinstance(cached.value, Some)
    assert cached.value.value.close == Decimal("99")


def test_latest_cached_returns_nothing_when_no_data_anywhere() -> None:
    provider = _FakeProvider()  # both empty
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    cached = src.latest_cached()
    assert isinstance(cached, Ok)
    assert isinstance(cached.value, Nothing)


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------


def test_yfinance_bar_source_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="bar_window_days"):
        YFinanceBarSource(
            provider=_FakeProvider(),
            instrument=_stock(),
            bar_window_days=0,
        )


# ---------------------------------------------------------------------------
# CR-022 — post-backfill polls route through ``fetch_live_bars``
# ---------------------------------------------------------------------------


def test_post_backfill_poll_prefers_fetch_live_bars() -> None:
    """REQ_F_PAP_010 / CR-022 — once the backfill queue drains, the
    bar source SHALL prefer ``provider.fetch_live_bars`` over the
    cache-backed ``provider.bars`` so the range-aware cache
    (CR-021) doesn't pin the panel to a stale envelope.
    """
    provider = _FakeLiveProvider(
        bar_response=[_bar(at=_T0, close="100")]
    )
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    src.next_bar()  # drains the backfill queue
    provider.live_response = [
        _bar(at=_T0, close="100"),
        _bar(at=_T0 + timedelta(days=1), close="111"),
    ]
    result = src.next_bar()
    assert isinstance(result, Ok) and isinstance(result.value, Some)
    assert result.value.value.close == Decimal("111")
    # ``fetch_live_bars`` was called for the post-backfill poll.
    assert provider.live_calls >= 1


def test_post_backfill_poll_falls_back_to_bars_when_no_live_hook() -> None:
    """Test fakes without ``fetch_live_bars`` SHALL stay on the
    legacy ``bars`` path so the bar source remains compatible
    with simulated providers."""
    provider = _FakeProvider(bar_response=[_bar(at=_T0, close="100")])
    src = YFinanceBarSource(provider=provider, instrument=_stock())
    src.next_bar()
    provider.bar_response = [
        _bar(at=_T0, close="100"),
        _bar(at=_T0 + timedelta(days=1), close="105"),
    ]
    result = src.next_bar()
    assert isinstance(result, Ok) and isinstance(result.value, Some)
    assert result.value.value.close == Decimal("105")


def test_force_network_false_stays_on_cache_path() -> None:
    """Operators that want strict replay (no network on the bar
    source either) opt out by setting ``force_network=False``."""
    provider = _FakeLiveProvider(
        bar_response=[_bar(at=_T0, close="100")]
    )
    src = YFinanceBarSource(
        provider=provider, instrument=_stock(), force_network=False
    )
    src.next_bar()  # drain backfill
    provider.live_response = [
        _bar(at=_T0 + timedelta(days=1), close="999")
    ]
    provider.bar_response = [
        _bar(at=_T0 + timedelta(days=1), close="105")
    ]
    result = src.next_bar()
    assert isinstance(result, Ok) and isinstance(result.value, Some)
    # 105 wins (came from bars, not the live hook).
    assert result.value.value.close == Decimal("105")
    assert provider.live_calls == 0
