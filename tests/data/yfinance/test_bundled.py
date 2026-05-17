"""Tests for ``trading_system.data.yfinance.bundled`` — MVP-1 of
CR-016 (bundled offline fixtures).

The bundled fixtures unblock the network-failure mode the
User-Manual v0.2 verification pass surfaced. Tests verify:

- The shipped fixture root contains the documented 3-stock
  starter set (ASML.AS / BNP.PA / SAN.PA).
- ``populate_cache_from_bundled_fixtures`` copies every fixture
  into an empty cache root, preserving the on-disk layout the
  ``YFinanceCache`` expects.
- Re-running against a populated cache is a silent no-op
  (idempotent).
- ``--overwrite`` forces replacement when fixture content drifts.
- Categorised Errs surface for missing fixture root + unwritable
  cache root.
- ``list_bundled_symbols`` returns the alphabetised starter set.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from trading_system.data.yfinance.bundled import (
    DEFAULT_FIXTURE_ROOT,
    list_bundled_symbols,
    populate_cache_from_bundled_fixtures,
)
from trading_system.result import Err, Ok


_EXPECTED_SYMBOLS = ("ASML.AS", "BNP.PA", "SAN.PA")


# ---------------------------------------------------------------------------
# Shipped-fixture-root sanity
# ---------------------------------------------------------------------------


def test_bundled_fixture_root_exists() -> None:
    assert DEFAULT_FIXTURE_ROOT.is_dir(), (
        f"bundled fixtures not found at {DEFAULT_FIXTURE_ROOT} — "
        "run `python tools/generate_bundled_fixtures.py` to regenerate"
    )


def test_bundled_fixture_root_carries_expected_symbols() -> None:
    symbols = list_bundled_symbols().unwrap()
    assert symbols == _EXPECTED_SYMBOLS


def test_each_bundled_symbol_has_bars_and_dividends() -> None:
    for symbol in _EXPECTED_SYMBOLS:
        symbol_dir = DEFAULT_FIXTURE_ROOT / symbol
        bars_files = list((symbol_dir / "1d").glob("*.jsonl"))
        divs_files = list((symbol_dir / "dividends").glob("*.jsonl"))
        assert len(bars_files) == 1, (
            f"{symbol}: expected exactly one 1d bars file, got {bars_files}"
        )
        assert len(divs_files) == 1, (
            f"{symbol}: expected exactly one dividends file, got {divs_files}"
        )


# ---------------------------------------------------------------------------
# populate_cache_from_bundled_fixtures — happy path
# ---------------------------------------------------------------------------


def test_populate_into_empty_cache_root(tmp_path: Path) -> None:
    """REQ_F_DAT_004 — every fixture file lands at the expected
    on-disk location the YFinanceCache reads from."""
    cache_root = tmp_path / "cache"
    result = populate_cache_from_bundled_fixtures(cache_root=cache_root)
    match result:
        case Ok(count):
            # 3 symbols × 2 files (bars + dividends) = 6.
            assert count == 6
        case Err(reason):
            raise AssertionError(reason)

    # Every fixture is now in the cache root with the same relative path.
    for symbol in _EXPECTED_SYMBOLS:
        bars = list((cache_root / symbol / "1d").glob("*.jsonl"))
        divs = list((cache_root / symbol / "dividends").glob("*.jsonl"))
        assert len(bars) == 1
        assert len(divs) == 1


def test_populate_creates_missing_cache_root(tmp_path: Path) -> None:
    cache_root = tmp_path / "deeply" / "nested" / "cache"
    result = populate_cache_from_bundled_fixtures(cache_root=cache_root)
    assert isinstance(result, Ok)
    assert cache_root.is_dir()


# ---------------------------------------------------------------------------
# Idempotence + overwrite semantics
# ---------------------------------------------------------------------------


def test_populate_is_idempotent(tmp_path: Path) -> None:
    """Running twice with the default ``overwrite=False`` SHALL
    silently skip already-present files (no error)."""
    cache_root = tmp_path / "cache"
    populate_cache_from_bundled_fixtures(cache_root=cache_root).unwrap()
    second = populate_cache_from_bundled_fixtures(cache_root=cache_root).unwrap()
    assert second == 6  # still 6 — counted including skipped


def test_populate_skips_existing_files_by_default(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    populate_cache_from_bundled_fixtures(cache_root=cache_root).unwrap()
    # Tamper with one file; default re-run SHALL NOT overwrite it.
    target = list(cache_root.rglob("*.jsonl"))[0]
    target.write_text("tampered\n")
    populate_cache_from_bundled_fixtures(cache_root=cache_root).unwrap()
    assert target.read_text() == "tampered\n"


def test_populate_overwrite_replaces_existing(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    populate_cache_from_bundled_fixtures(cache_root=cache_root).unwrap()
    target = list(cache_root.rglob("*.jsonl"))[0]
    target.write_text("tampered\n")
    populate_cache_from_bundled_fixtures(
        cache_root=cache_root, overwrite=True
    ).unwrap()
    assert target.read_text() != "tampered\n"


# ---------------------------------------------------------------------------
# Custom fixture root
# ---------------------------------------------------------------------------


def test_populate_from_custom_fixture_root(tmp_path: Path) -> None:
    """Operators can point at a private fixture root (e.g., a
    synced corporate dataset). The default root is the bundled
    starter set; the function accepts any other layout-compatible
    directory."""
    fixture_root = tmp_path / "custom-fixtures"
    cache_root = tmp_path / "cache"
    # Copy the bundled fixtures into a custom location.
    shutil.copytree(DEFAULT_FIXTURE_ROOT, fixture_root)
    result = populate_cache_from_bundled_fixtures(
        cache_root=cache_root, fixture_root=fixture_root
    )
    assert isinstance(result, Ok)


# ---------------------------------------------------------------------------
# Categorised Errs
# ---------------------------------------------------------------------------


def test_missing_fixture_root_returns_err(tmp_path: Path) -> None:
    match populate_cache_from_bundled_fixtures(
        cache_root=tmp_path / "cache",
        fixture_root=tmp_path / "nonexistent",
    ):
        case Err(reason):
            assert reason.startswith("data:fixture_root_missing:")
        case _:
            raise AssertionError("expected Err")


def test_list_bundled_symbols_missing_root(tmp_path: Path) -> None:
    match list_bundled_symbols(fixture_root=tmp_path / "ghost"):
        case Err(reason):
            assert reason.startswith("data:fixture_root_missing:")
        case _:
            raise AssertionError("expected Err")


def test_list_bundled_symbols_sorted(tmp_path: Path) -> None:
    """REQ_NF_DET_001 family — alphabetical ordering for replay
    determinism."""
    symbols = list_bundled_symbols().unwrap()
    assert list(symbols) == sorted(symbols)


# ---------------------------------------------------------------------------
# Cache integration — the YFinanceCache reads the populated files
# ---------------------------------------------------------------------------


def test_populated_cache_is_readable_by_yfinance_cache(tmp_path: Path) -> None:
    """Confirm the on-disk layout matches what ``YFinanceCache``
    expects: a `get_bars` call against the bundled-fixture range
    returns the bars without a network attempt."""
    from datetime import UTC, datetime

    from trading_system.data.yfinance.cache import CacheKey, YFinanceCache

    cache_root = tmp_path / "cache"
    populate_cache_from_bundled_fixtures(cache_root=cache_root).unwrap()
    cache = YFinanceCache(root=cache_root)
    # The bundled fixture window — matches tools/generate_bundled_fixtures.py.
    key = CacheKey(
        symbol="ASML.AS",
        timeframe="1d",
        start=datetime(2024, 1, 2, tzinfo=UTC),
        end=datetime(2024, 12, 31, tzinfo=UTC),
    )
    result = cache.get_bars(key)
    assert result.is_some(), "bundled fixture cache miss — layout mismatch"
    bars = result.unwrap()
    assert len(bars) > 200  # ~261 weekdays in 2024
    # Sanity — first bar is in 2024.
    assert bars[0].at.year == 2024
