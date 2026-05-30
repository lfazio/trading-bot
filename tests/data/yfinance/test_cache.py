"""Tests for ``trading_system.data.yfinance.cache``.

Covers TC_DAT_007 (cache survives restart) and the supporting
invariants for TC_DAT_004 / TC_DAT_005 / TC_DAT_006 / TC_DAT_015 at
the cache layer.

REQ refs: REQ_F_DAT_004, REQ_F_DAT_005, REQ_NF_DAT_001,
REQ_SDD_DAT_010, REQ_SDD_DAT_012.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trading_system.data.types import Bar
from trading_system.data.yfinance.cache import CacheKey, YFinanceCache
from trading_system.models.identifiers import InstrumentId
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Dividend
from trading_system.result import Nothing, Ok, Some

EUR = Currency.EUR


def _key(
    symbol: str = "ASML.AS",
    timeframe: str = "1d",
    start: datetime | None = None,
    end: datetime | None = None,
) -> CacheKey:
    return CacheKey(
        symbol=symbol,
        timeframe=timeframe,
        start=start or datetime(2026, 1, 1, tzinfo=UTC),
        end=end or datetime(2026, 1, 5, tzinfo=UTC),
    )


def _bar(day: int, close: str = "100") -> Bar:
    p = Decimal(close)
    return Bar(
        at=datetime(2026, 1, day, tzinfo=UTC),
        open=p,
        high=p,
        low=p,
        close=p,
        volume=Decimal(1000),
    )


def _dividend(year: int = 2026, month: int = 6, day: int = 15, amount: str = "0.50") -> Dividend:
    ts = datetime(year, month, day, tzinfo=UTC)
    return Dividend(
        instrument=InstrumentId("ASML.AS"),
        ex_date=ts,
        pay_date=ts,
        amount_gross=Money(Decimal(amount), EUR),
    )


# ---------------------------------------------------------------------------
# Bars round-trip
# ---------------------------------------------------------------------------


class TestBarsCache:
    def test_get_returns_nothing_when_missing(self, tmp_path: Path) -> None:
        cache = YFinanceCache(root=tmp_path)
        assert cache.get_bars(_key()) == Nothing()
        assert cache.has_bars(_key()) is False

    def test_put_then_get_round_trips(self, tmp_path: Path) -> None:
        cache = YFinanceCache(root=tmp_path)
        bars = [_bar(2), _bar(3), _bar(4)]
        match cache.put_bars(_key(), bars):
            case Ok(_):
                pass
            case _:
                raise AssertionError("put_bars failed")
        assert cache.has_bars(_key()) is True
        match cache.get_bars(_key()):
            case Some(loaded):
                assert loaded == bars
            case Nothing():
                raise AssertionError("expected Some(bars)")

    def test_decimal_precision_preserved(self, tmp_path: Path) -> None:
        # Cents-level prices and a fractional volume — make sure
        # nothing rounds in transit.
        cache = YFinanceCache(root=tmp_path)
        bar = Bar(
            at=datetime(2026, 1, 2, tzinfo=UTC),
            open=Decimal("100.123456"),
            high=Decimal("101.987654"),
            low=Decimal("99.111111"),
            close=Decimal("100.500000"),
            volume=Decimal("1234567"),
        )
        cache.put_bars(_key(), [bar])
        match cache.get_bars(_key()):
            case Some([loaded]):
                assert loaded.open == Decimal("100.123456")
                assert loaded.high == Decimal("101.987654")
                assert loaded.close == Decimal("100.500000")
            case _:
                raise AssertionError("expected one bar")

    def test_survives_process_restart_via_fresh_instance(self, tmp_path: Path) -> None:
        # TC_DAT_007: a fresh YFinanceCache instance over the same
        # root directory reads what an earlier instance wrote.
        bars = [_bar(2), _bar(3)]
        YFinanceCache(root=tmp_path).put_bars(_key(), bars)
        # Discard and reconstruct.
        fresh = YFinanceCache(root=tmp_path)
        assert fresh.get_bars(_key()) == Some(bars)

    def test_two_keys_dont_collide(self, tmp_path: Path) -> None:
        cache = YFinanceCache(root=tmp_path)
        k1 = _key(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 5, tzinfo=UTC))
        k2 = _key(start=datetime(2026, 1, 6, tzinfo=UTC), end=datetime(2026, 1, 10, tzinfo=UTC))
        cache.put_bars(k1, [_bar(2)])
        cache.put_bars(k2, [_bar(7)])
        assert cache.get_bars(k1) == Some([_bar(2)])
        assert cache.get_bars(k2) == Some([_bar(7)])

    def test_corrupt_file_is_treated_as_miss(self, tmp_path: Path) -> None:
        cache = YFinanceCache(root=tmp_path)
        cache.put_bars(_key(), [_bar(2)])
        # Stomp the file with garbage.
        cache._bars_path(_key()).write_text("{not json")  # type: ignore[attr-defined]
        assert cache.get_bars(_key()) == Nothing()


# ---------------------------------------------------------------------------
# Dividends round-trip
# ---------------------------------------------------------------------------


class TestDividendsCache:
    def test_round_trip(self, tmp_path: Path) -> None:
        cache = YFinanceCache(root=tmp_path)
        divs = [_dividend(month=3), _dividend(month=9)]
        cache.put_dividends("ASML.AS", 2026, divs)
        match cache.get_dividends("ASML.AS", 2026, EUR):
            case Some(loaded):
                assert loaded == divs
            case _:
                raise AssertionError("expected Some")

    def test_currency_mismatch_treated_as_miss(self, tmp_path: Path) -> None:
        # Stored in EUR; reader asks for USD — cache returns Nothing
        # rather than silently misrepresenting the currency.
        cache = YFinanceCache(root=tmp_path)
        cache.put_dividends("ASML.AS", 2026, [_dividend()])
        assert cache.get_dividends("ASML.AS", 2026, Currency.USD) == Nothing()


# ---------------------------------------------------------------------------
# CacheKey identity
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_equality_requires_full_match(self) -> None:
        k1 = _key()
        k2 = _key()
        assert k1 == k2
        k3 = _key(end=datetime(2026, 1, 6, tzinfo=UTC))
        assert k1 != k3

    def test_filename_is_stable(self) -> None:
        k = _key()
        # Calling twice yields the same string.
        assert k.filename() == k.filename()

    def test_filename_strips_unsafe_chars(self) -> None:
        k = _key()
        name = k.filename()
        assert ":" not in name
        assert "+" not in name
        assert name.endswith("_bars.jsonl")


def test_constructor_must_accept_path(tmp_path: Path) -> None:
    # Sanity: YFinanceCache builds; root doesn't have to exist yet.
    fresh = tmp_path / "new_cache"
    cache = YFinanceCache(root=fresh)
    assert isinstance(cache.root, Path)
    # First write creates the directory tree.
    cache.put_bars(_key(), [_bar(2)])
    assert (fresh / "ASML.AS" / "1d").is_dir()


def test_pytest_imports_dont_pull_yfinance() -> None:
    # The cache + mappers + symbols path SHALL NOT pull yfinance into
    # the test environment (REQ_F_DAT_006 / REQ_SDS_DAT_002 spirit:
    # tests run with allow_network=False semantics by default).
    assert "yfinance" not in sys.modules
    assert "pandas" not in sys.modules


# ---------------------------------------------------------------------------
# CR-021 — range-aware lookup (TC_DAT_C1_001..004)
# ---------------------------------------------------------------------------


class TestRangeAwareLookup:
    """REQ_SDD_DAT_014 — on exact-key miss, scan the symbol/timeframe
    dir for any cached file whose stored window envelopes the
    requested range, slice the bars inside, return.

    REQ_NF_DAT_004 — the sliced read SHALL be byte-equal (same Bar
    values, same order) to what an exact-key recorder run for
    ``[key.start, key.end]`` would have produced. The
    ``test_envelope_hit_slices_to_requested_range`` test exercises
    that invariant directly: a recorder run that wrote one bar per
    day for Jan 2..Jan 8 SHALL produce the same four-bar list as
    the envelope-sliced read for Jan 3..Jan 6.
    """

    def _envelope_key(self, start: datetime, end: datetime) -> CacheKey:
        return CacheKey(
            symbol="ASML.AS", timeframe="1d", start=start, end=end
        )

    def test_exact_key_hit_still_byte_identical(self, tmp_path: Path) -> None:
        """The exact-key fast path SHALL be unchanged."""
        cache = YFinanceCache(root=tmp_path)
        cache.put_bars(_key(), [_bar(2), _bar(3), _bar(4)])
        match cache.get_bars(_key()):
            case Some(bars):
                assert bars == [_bar(2), _bar(3), _bar(4)]
            case _:
                raise AssertionError("expected Some(bars)")

    def test_envelope_hit_slices_to_requested_range(self, tmp_path: Path) -> None:
        """A wider cached file SHALL satisfy a narrower query."""
        cache = YFinanceCache(root=tmp_path)
        # Cache an envelope Jan 1..Jan 10 with bars on days 2..8.
        envelope_key = self._envelope_key(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 10, tzinfo=UTC),
        )
        cache.put_bars(envelope_key, [_bar(d) for d in range(2, 9)])
        # Query for a sub-window Jan 3..Jan 6.
        narrow = self._envelope_key(
            datetime(2026, 1, 3, tzinfo=UTC),
            datetime(2026, 1, 6, tzinfo=UTC),
        )
        match cache.get_bars(narrow):
            case Some(bars):
                assert [b.at.day for b in bars] == [3, 4, 5, 6]
            case _:
                raise AssertionError("expected envelope hit")

    def test_no_enveloping_file_returns_nothing(self, tmp_path: Path) -> None:
        """A query partially outside every cached envelope SHALL
        return Nothing (no partial-coverage merge in v1)."""
        cache = YFinanceCache(root=tmp_path)
        # Only days 2..4 are cached.
        envelope_key = self._envelope_key(
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 4, tzinfo=UTC),
        )
        cache.put_bars(envelope_key, [_bar(2), _bar(3), _bar(4)])
        # Query asks for Jan 1..Jan 5 — wider than any cached file.
        wide = self._envelope_key(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 5, tzinfo=UTC),
        )
        assert cache.get_bars(wide) == Nothing()

    def test_widest_envelope_wins_when_multiple_match(self, tmp_path: Path) -> None:
        """When several cached files envelope the request, the
        widest (largest end-start span) is chosen — that's
        ``CR-021`` open question (2)."""
        cache = YFinanceCache(root=tmp_path)
        # Narrow envelope Jan 2..Jan 5; wide envelope Jan 1..Jan 10.
        narrow_key = self._envelope_key(
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 5, tzinfo=UTC),
        )
        wide_key = self._envelope_key(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 10, tzinfo=UTC),
        )
        cache.put_bars(narrow_key, [_bar(3, close="999")])  # poisoned
        cache.put_bars(wide_key, [_bar(d) for d in range(2, 9)])
        # Query Jan 3..Jan 3 — both envelopes cover; widest wins.
        query = self._envelope_key(
            datetime(2026, 1, 3, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
        )
        match cache.get_bars(query):
            case Some([bar]):
                # From the wide envelope, close == "100" (default).
                assert bar.close == Decimal("100")
            case _:
                raise AssertionError("expected widest envelope hit")

    def test_naive_key_compares_against_tz_aware_filename_window(
        self, tmp_path: Path
    ) -> None:
        """Regression — when the caller's ``CacheKey`` carries naïve
        datetimes (e.g., from ``state.at - timedelta(...)`` against a
        naïve tick) and the cached filename window is tz-aware, the
        envelope predicate SHALL promote both sides to UTC instead
        of raising ``TypeError: can't compare offset-naive and
        offset-aware datetimes``."""
        cache = YFinanceCache(root=tmp_path)
        # Seed a tz-aware cached envelope Jan 1..Jan 10.
        envelope_key = self._envelope_key(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 10, tzinfo=UTC),
        )
        cache.put_bars(envelope_key, [_bar(d) for d in range(2, 9)])
        # Query with NAÏVE datetimes — should not raise + should
        # surface the same envelope hit.
        naive_query = CacheKey(
            symbol="ASML.AS",
            timeframe="1d",
            start=datetime(2026, 1, 3),  # naïve
            end=datetime(2026, 1, 6),  # naïve
        )
        match cache.get_bars(naive_query):
            case Some(bars):
                assert [b.at.day for b in bars] == [3, 4, 5, 6]
            case _:
                raise AssertionError("expected envelope hit on naïve key")

    def test_naive_datetime_in_cache_normalised_on_read(
        self, tmp_path: Path
    ) -> None:
        """Older recorder runs wrote naïve datetimes; the read path
        SHALL surface them as UTC-aware so the envelope predicate
        compares uniformly."""
        cache = YFinanceCache(root=tmp_path)
        # Write a cache file with a hand-crafted naïve timestamp.
        sym_dir = tmp_path / "ASML.AS" / "1d"
        sym_dir.mkdir(parents=True)
        naive_file = sym_dir / (
            "2026-01-01T000000__2026-01-10T000000_bars.jsonl"
        )
        naive_file.write_text(
            '{"at":"2026-01-03T00:00:00","open":"100","high":"100",'
            '"low":"100","close":"100","volume":"1000"}\n',
            encoding="utf-8",
        )
        # Range-aware query — picks up the naïve file and slices.
        query = self._envelope_key(
            datetime(2026, 1, 3, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
        )
        match cache.get_bars(query):
            case Some([bar]):
                assert bar.at.tzinfo is UTC
            case _:
                raise AssertionError("expected UTC-normalised bar")
