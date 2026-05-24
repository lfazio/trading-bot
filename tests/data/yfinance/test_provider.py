"""Tests for ``trading_system.data.yfinance.provider``.

Covers:
- TC_DAT_004 — cache hit returns bars and opens no network connection.
- TC_DAT_005 — cache miss with allow_network=False returns
  Err("data:cache_miss_offline:...").
- TC_DAT_006 — cache miss with allow_network=True downloads,
  persists, and a follow-up call hits the cache (no second download).
- TC_DAT_011 — run_mode="live" panics at construction.
- TC_DAT_013 — fundamentals returns Err("data:not_supported:...").
- TC_DAT_014 — transient errors retry up to 3 times with backoff.
- TC_DAT_015 — cache pin: cached bars survive an upstream revision.
- TC_DAT_016 — latest() never opens a network connection.

REQ refs:
- REQ_F_DAT_001 — adapter exists.
- REQ_F_DAT_005 — cache hit reads pure-disk.
- REQ_F_DAT_006 — allow_network gate (default False).
- REQ_F_DAT_009 — run_mode=="live" panic at construction.
- REQ_F_DAT_010 — fundamentals not supported.
- REQ_NF_DAT_001 — cache as system of record for replay determinism.
- REQ_SDS_DAT_001 — Protocol-only dependency surface.
- REQ_SDS_DAT_002 — cache as system of record.
- REQ_SDS_DAT_004 — panic-on-live-mode.
- REQ_SDD_DAT_012 — closed error category set with retry.
- REQ_SDD_DAT_013 — latest() is offline-only.
- REQ_SDD_ERR_005 — exponential-backoff retry pattern.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from trading_system.data.types import Bar, Timeframe
from trading_system.data.yfinance.cache import CacheKey, YFinanceCache
from trading_system.data.yfinance.provider import (
    TransientDownloadError,
    YFinanceMarketDataProvider,
)
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err, Ok

EUR = Currency.EUR


def _stock(symbol: str = "ASML", exchange: str = "AS") -> Stock:
    return Stock(
        id=InstrumentId(f"{symbol}.{exchange}"),
        symbol=symbol,
        exchange=exchange,
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _ts(year: int = 2026, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Row:
    Index: Any
    Open: Any
    High: Any
    Low: Any
    Close: Any
    Volume: Any


@dataclass(slots=True)
class _DF:
    rows: list[_Row]
    empty: bool = False

    def itertuples(self) -> list[_Row]:
        return self.rows


def _df_for(start_day: int, count: int) -> _DF:
    rows = []
    for i in range(count):
        close = Decimal("100") + Decimal(i)
        rows.append(
            _Row(
                Index=_ts(2026, 1, start_day + i),
                Open=Decimal("100"),
                High=close + Decimal("1"),
                Low=Decimal("99"),
                Close=close,
                Volume=Decimal("1000"),
            )
        )
    return _DF(rows=rows)


@dataclass(slots=True)
class _RecordingDownloader:
    """Counts calls and returns canned results in order."""

    results: list[Any]
    calls: list[tuple[str, str, datetime, datetime]]

    def __call__(self, symbol: str, tf: str, start: datetime, end: datetime) -> Any:
        self.calls.append((symbol, tf, start, end))
        return self.results.pop(0)


def _no_sleep(_: float) -> None:
    return None


def _build_provider(tmp_path: Path, *, allow_network: bool, downloader: Any = None):
    cache_obj = YFinanceCache(root=tmp_path)
    if downloader is None:
        downloader = _RecordingDownloader(results=[], calls=[])
    return (
        cache_obj,
        downloader,
        YFinanceMarketDataProvider(
            cache=cache_obj,
            currency=EUR,
            allow_network=allow_network,
            bar_downloader=downloader,
            backoff_sleep=_no_sleep,
        ),
    )


# ----------------------------------------------------------------------
# TC_DAT_011 — live-mode panic
# ----------------------------------------------------------------------


def test_live_mode_construction_panics(tmp_path: Path) -> None:
    cache = YFinanceCache(root=tmp_path)
    with pytest.raises(RuntimeError, match="forbidden in live mode"):
        YFinanceMarketDataProvider(
            cache=cache,
            currency=EUR,
            run_mode="live",
        )


def test_paper_mode_does_not_panic(tmp_path: Path) -> None:
    # Anything other than "live" is allowed; backtest is the default.
    cache = YFinanceCache(root=tmp_path)
    YFinanceMarketDataProvider(cache=cache, currency=EUR, run_mode="paper")
    YFinanceMarketDataProvider(cache=cache, currency=EUR)  # default


# ----------------------------------------------------------------------
# TC_DAT_004 / TC_DAT_005 — cache hit + offline-miss
# ----------------------------------------------------------------------


def test_cache_hit_returns_bars_no_download(tmp_path: Path) -> None:
    bars = [
        Bar(
            at=_ts(2026, 1, 2),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("0"),
        )
    ]
    cache = YFinanceCache(root=tmp_path)
    key = CacheKey(symbol="ASML.AS", timeframe="1d", start=_ts(2026, 1, 1), end=_ts(2026, 1, 5))
    cache.put_bars(key, bars)

    downloader = _RecordingDownloader(results=[], calls=[])
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=EUR,
        allow_network=False,
        bar_downloader=downloader,
    )
    res = provider.bars(_stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5))
    match res:
        case Ok(loaded):
            assert loaded == bars
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")
    # Critical: the downloader was NEVER called.
    assert downloader.calls == []


def test_cache_miss_offline_returns_err(tmp_path: Path) -> None:
    _, downloader, provider = _build_provider(tmp_path, allow_network=False)
    res = provider.bars(_stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5))
    match res:
        case Err(reason):
            assert reason == "data:cache_miss_offline:ASML.AS"
        case Ok(_):
            raise AssertionError("expected Err")
    assert downloader.calls == []  # type: ignore[attr-defined]


# ----------------------------------------------------------------------
# TC_DAT_006 — cache miss with network: download, persist, follow-up hits cache
# ----------------------------------------------------------------------


def test_cache_miss_with_network_downloads_persists_then_hits(tmp_path: Path) -> None:
    df = _df_for(2, 3)
    downloader = _RecordingDownloader(results=[df], calls=[])
    cache = YFinanceCache(root=tmp_path)
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=EUR,
        allow_network=True,
        bar_downloader=downloader,
        backoff_sleep=_no_sleep,
    )
    # First call: cache miss -> download.
    res1 = provider.bars(_stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5))
    match res1:
        case Ok(bars1):
            assert len(bars1) == 3
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")
    assert len(downloader.calls) == 1

    # Second call with identical args: cache hit; no second download.
    res2 = provider.bars(_stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5))
    match res2:
        case Ok(bars2):
            assert bars2 == bars1
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")
    assert len(downloader.calls) == 1, "second call should NOT re-download"


# ----------------------------------------------------------------------
# TC_DAT_015 — cache pin: previously-cached bars survive upstream revision
# ----------------------------------------------------------------------


def test_cache_pins_against_upstream_revision(tmp_path: Path) -> None:
    original_df = _df_for(2, 3)
    revised_df = _df_for(2, 5)  # different shape on the upstream side

    downloader = _RecordingDownloader(results=[original_df, revised_df], calls=[])
    cache = YFinanceCache(root=tmp_path)
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=EUR,
        allow_network=True,
        bar_downloader=downloader,
        backoff_sleep=_no_sleep,
    )
    args = (_stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5))
    first = provider.bars(*args).unwrap()
    second = provider.bars(*args).unwrap()
    # Cache hit on the second call — still returns the original 3 bars,
    # NOT the revised 5; downloader was called only once.
    assert len(first) == 3
    assert second == first
    assert len(downloader.calls) == 1


# ----------------------------------------------------------------------
# TC_DAT_014 — retry policy
# ----------------------------------------------------------------------


def test_transient_error_retried_up_to_three_times(tmp_path: Path) -> None:
    attempts: list[int] = []

    def flaky(_sym: str, _tf: str, _s: datetime, _e: datetime) -> Any:
        attempts.append(1)
        if len(attempts) < 3:
            raise TransientDownloadError("data:rate_limited:throttled")
        return _df_for(2, 1)

    cache = YFinanceCache(root=tmp_path)
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=EUR,
        allow_network=True,
        bar_downloader=flaky,
        backoff_sleep=_no_sleep,
    )
    res = provider.bars(_stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5))
    assert isinstance(res, Ok)
    assert len(attempts) == 3  # two transient failures + one success


def test_persistent_transient_error_surfaces_after_three_attempts(tmp_path: Path) -> None:
    attempts: list[int] = []

    def always_429(_sym: str, _tf: str, _s: datetime, _e: datetime) -> Any:
        attempts.append(1)
        raise TransientDownloadError("data:rate_limited:throttled")

    cache = YFinanceCache(root=tmp_path)
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=EUR,
        allow_network=True,
        bar_downloader=always_429,
        backoff_sleep=_no_sleep,
    )
    res = provider.bars(_stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5))
    match res:
        case Err(reason):
            assert reason.startswith("data:rate_limited")
        case Ok(_):
            raise AssertionError("expected Err")
    assert len(attempts) == 3  # exactly 3 tries, no more


# ----------------------------------------------------------------------
# TC_DAT_013 — fundamentals not supported
# ----------------------------------------------------------------------


def test_fundamentals_returns_not_supported(tmp_path: Path) -> None:
    *_, provider = _build_provider(tmp_path, allow_network=False)
    res = provider.fundamentals(_stock())
    match res:
        case Err(reason):
            assert reason == "data:not_supported:fundamentals_via_yfinance"
        case Ok(_):
            raise AssertionError("expected Err")


# ----------------------------------------------------------------------
# TC_DAT_016 — latest() is offline-only
# ----------------------------------------------------------------------


class TestLatestOfflineOnly:
    def test_returns_not_found_when_cache_empty(self, tmp_path: Path) -> None:
        *_, provider = _build_provider(tmp_path, allow_network=True)
        # allow_network=True must NOT cause latest() to fetch.
        res = provider.latest(_stock())
        match res:
            case Err(reason):
                assert reason.startswith("data:not_found")
            case Ok(_):
                raise AssertionError("expected Err")

    def test_returns_latest_cached_bar(self, tmp_path: Path) -> None:
        cache = YFinanceCache(root=tmp_path)
        # Two cached ranges; the second's last bar is the global latest.
        early = [
            Bar(
                at=_ts(2026, 1, 2),
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("0"),
            )
        ]
        recent = [
            Bar(
                at=_ts(2026, 6, 1),
                open=Decimal("110"),
                high=Decimal("110"),
                low=Decimal("110"),
                close=Decimal("110"),
                volume=Decimal("0"),
            )
        ]
        cache.put_bars(
            CacheKey("ASML.AS", "1d", _ts(2026, 1, 1), _ts(2026, 1, 5)),
            early,
        )
        cache.put_bars(
            CacheKey("ASML.AS", "1d", _ts(2026, 5, 1), _ts(2026, 6, 5)),
            recent,
        )
        downloader = _RecordingDownloader(results=[], calls=[])
        provider = YFinanceMarketDataProvider(
            cache=cache,
            currency=EUR,
            allow_network=True,  # explicitly TRUE; must still not download
            bar_downloader=downloader,
        )
        res = provider.latest(_stock())
        match res:
            case Ok(bar):
                assert bar.at == _ts(2026, 6, 1)
            case Err(e):
                raise AssertionError(f"unexpected Err: {e}")
        assert downloader.calls == [], "latest() must not download"


# ----------------------------------------------------------------------
# CR-022 — fetch_live_bars bypass-cache fetch
# ----------------------------------------------------------------------


def test_fetch_live_bars_hits_network_even_when_cache_has_envelope(
    tmp_path: Path,
) -> None:
    """REQ_SDD_DAT_015 — ``fetch_live_bars`` SHALL call the network
    even when the CR-021 envelope cache could satisfy the request,
    so the paper-trading bar source sees fresh bars on each poll.
    """
    # Pre-populate the cache with stale bars covering the window…
    bars = [
        Bar(
            at=_ts(2026, 1, 2),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("0"),
        )
    ]
    cache = YFinanceCache(root=tmp_path)
    key = CacheKey(
        symbol="ASML.AS",
        timeframe="1d",
        start=_ts(2026, 1, 1),
        end=_ts(2026, 1, 5),
    )
    cache.put_bars(key, bars)
    # …and have the downloader return fresher bars.
    downloader = _RecordingDownloader(
        results=[_df_for(start_day=3, count=3)],
        calls=[],
    )
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=EUR,
        allow_network=True,
        bar_downloader=downloader,
        backoff_sleep=_no_sleep,
    )
    res = provider.fetch_live_bars(
        _stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5)
    )
    match res:
        case Ok(received):
            # 3 fresh bars from the network — not the stale single
            # cache entry.
            assert len(received) == 3
            assert received[0].at == _ts(2026, 1, 3)
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")
    assert downloader.calls, "fetch_live_bars SHALL hit the network"


def test_fetch_live_bars_falls_back_to_cache_on_network_failure(
    tmp_path: Path,
) -> None:
    """REQ_F_PAP_002 — graceful degradation. When the network is
    unavailable, ``fetch_live_bars`` SHALL return the cached
    envelope so the paper runtime keeps surfacing the last-known
    bars instead of an upstream-blocked Err."""
    bars = [
        Bar(
            at=_ts(2026, 1, 2),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("0"),
        )
    ]
    cache = YFinanceCache(root=tmp_path)
    key = CacheKey(
        symbol="ASML.AS",
        timeframe="1d",
        start=_ts(2026, 1, 1),
        end=_ts(2026, 1, 5),
    )
    cache.put_bars(key, bars)

    def _fail(*_args: Any, **_kw: Any) -> Any:
        raise TransientDownloadError("data:network:fake-timeout")

    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=EUR,
        allow_network=True,
        bar_downloader=_fail,
        backoff_sleep=_no_sleep,
    )
    res = provider.fetch_live_bars(
        _stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5)
    )
    match res:
        case Ok(received):
            assert received == bars
        case Err(e):
            raise AssertionError(f"expected cache fallback, got Err: {e}")


def test_fetch_live_bars_offline_uses_cache_only(tmp_path: Path) -> None:
    """``allow_network=False`` SHALL keep ``fetch_live_bars`` on the
    cache path (no network attempt) — operators that want strict
    replay opt out of the live bypass."""
    bars = [
        Bar(
            at=_ts(2026, 1, 2),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("0"),
        )
    ]
    cache = YFinanceCache(root=tmp_path)
    key = CacheKey(
        symbol="ASML.AS",
        timeframe="1d",
        start=_ts(2026, 1, 1),
        end=_ts(2026, 1, 5),
    )
    cache.put_bars(key, bars)
    downloader = _RecordingDownloader(results=[], calls=[])
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=EUR,
        allow_network=False,
        bar_downloader=downloader,
        backoff_sleep=_no_sleep,
    )
    res = provider.fetch_live_bars(
        _stock(), Timeframe.D1, _ts(2026, 1, 1), _ts(2026, 1, 5)
    )
    match res:
        case Ok(received):
            assert received == bars
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")
    assert downloader.calls == [], "allow_network=False SHALL skip network"


# ----------------------------------------------------------------------
# Hermetic test environment — no yfinance / pandas import on this path
# ----------------------------------------------------------------------


def test_test_environment_remains_hermetic() -> None:
    # The full file's tests run without pulling in yfinance or
    # pandas. The default downloaders import yfinance lazily inside
    # _yf_download; tests inject fakes and never trigger it.
    assert "yfinance" not in sys.modules
    assert "pandas" not in sys.modules
