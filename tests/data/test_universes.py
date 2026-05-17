"""Tests for ``trading_system.data.universes`` — MVP-3 of CR-016.

The universe loader is the operator's entry point for selecting a
named list of stocks for the screener + backtester. Tests verify:

- The two shipped presets (`eu-dividend-starter`, `cac40`) load
  cleanly + carry the expected symbols.
- Alphabetical Stock-id ordering is enforced for replay
  determinism.
- Categorised Errs surface for malformed YAML / missing fields /
  bad currency / duplicate ids / name mismatch.
- `list_bundled_universes` returns the alphabetised set.
"""

from __future__ import annotations

from pathlib import Path

from trading_system.data.universes import (
    DEFAULT_UNIVERSE_ROOT,
    Universe,
    list_bundled_universes,
    load_universe,
)
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err, Ok


# ---------------------------------------------------------------------------
# Shipped-preset sanity
# ---------------------------------------------------------------------------


def test_default_root_exists() -> None:
    assert DEFAULT_UNIVERSE_ROOT.is_dir(), (
        f"universe presets not found at {DEFAULT_UNIVERSE_ROOT}"
    )


def test_lists_shipped_presets() -> None:
    names = list_bundled_universes().unwrap()
    assert "eu-dividend-starter" in names
    assert "cac40" in names


def test_lists_presets_alphabetical() -> None:
    names = list_bundled_universes().unwrap()
    assert list(names) == sorted(names)


# ---------------------------------------------------------------------------
# eu-dividend-starter happy path
# ---------------------------------------------------------------------------


def test_load_eu_dividend_starter() -> None:
    uni = load_universe("eu-dividend-starter").unwrap()
    assert uni.name == "eu-dividend-starter"
    assert "starter" in uni.description.lower()
    # Three stocks aligned with the bundled fixtures.
    ids = [str(s.id) for s in uni.stocks]
    assert ids == ["ASML.AS", "BNP.PA", "SAN.PA"]
    # Every entry is a Stock with InstrumentClass.STOCK.
    for s in uni.stocks:
        assert isinstance(s, Stock)
        assert s.cls is InstrumentClass.STOCK
        assert s.currency is Currency.EUR


def test_load_cac40_returns_expected_subset() -> None:
    uni = load_universe("cac40").unwrap()
    assert uni.name == "cac40"
    # Subset includes the three starter stocks + AI / DG / MC etc.
    ids = {str(s.id) for s in uni.stocks}
    assert {"ASML.AS", "BNP.PA", "SAN.PA"}.issubset(ids)
    assert "MC.PA" in ids
    assert "TTE.PA" in ids
    # Alphabetical-by-id order is enforced.
    sorted_ids = sorted(ids)
    assert [str(s.id) for s in uni.stocks] == sorted_ids


# ---------------------------------------------------------------------------
# Universe invariants
# ---------------------------------------------------------------------------


def _stock(id: str) -> Stock:
    return Stock(
        id=InstrumentId(id),
        symbol=id.split(".")[0],
        exchange=id.split(".")[1] if "." in id else "AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="XS0000000001",
        sector="tech",
        country="NL",
    )


def test_universe_rejects_empty_name() -> None:
    import pytest

    with pytest.raises(ValueError, match="name"):
        Universe(name="", description="x", stocks=(_stock("ASML.AS"),))


def test_universe_rejects_empty_stocks() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least one stock"):
        Universe(name="x", description="", stocks=())


def test_universe_rejects_unsorted_stocks() -> None:
    import pytest

    with pytest.raises(ValueError, match="sorted alphabetically"):
        Universe(
            name="x",
            description="",
            stocks=(_stock("ZZZ.AS"), _stock("AAA.AS")),
        )


def test_universe_rejects_duplicate_stock_id() -> None:
    import pytest

    with pytest.raises(ValueError, match="duplicate"):
        Universe(
            name="x",
            description="",
            stocks=(_stock("AAA.AS"), _stock("AAA.AS")),
        )


# ---------------------------------------------------------------------------
# load_universe error categories
# ---------------------------------------------------------------------------


def test_load_unknown_universe_returns_io_err() -> None:
    match load_universe("ghost-universe-does-not-exist"):
        case Err(reason):
            assert reason.startswith("config:io:")
        case _:
            raise AssertionError("expected Err")


def test_load_empty_name_returns_schema_err() -> None:
    match load_universe(""):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_malformed_yaml_returns_parse_err(tmp_path: Path) -> None:
    _write(tmp_path, "broken", "name: broken\nstocks: {invalid\n")
    match load_universe("broken", universe_root=tmp_path):
        case Err(reason):
            assert reason.startswith("config:parse:")
        case _:
            raise AssertionError("expected Err")


def test_load_non_mapping_top_returns_schema_err(tmp_path: Path) -> None:
    _write(tmp_path, "wrong", "- one\n- two\n")
    match load_universe("wrong", universe_root=tmp_path):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_load_name_mismatch_returns_schema_err(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "alpha",
        "name: beta\nstocks: [{id: a, symbol: a, exchange: A, currency: EUR, isin: x, sector: x, country: x}]\n",
    )
    match load_universe("alpha", universe_root=tmp_path):
        case Err(reason):
            assert reason.startswith("config:schema:") and "name" in reason
        case _:
            raise AssertionError("expected Err")


def test_load_missing_stock_field_returns_schema_err(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "x",
        "name: x\nstocks:\n  - {id: a, symbol: a}\n",  # missing many fields
    )
    match load_universe("x", universe_root=tmp_path):
        case Err(reason):
            assert reason.startswith("config:schema:") and "missing" in reason
        case _:
            raise AssertionError("expected Err")


def test_load_bad_currency_returns_invariant_err(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "x",
        "name: x\nstocks:\n  - {id: a, symbol: a, exchange: A, currency: XYZ, isin: x, sector: x, country: x}\n",
    )
    match load_universe("x", universe_root=tmp_path):
        case Err(reason):
            assert reason.startswith("config:invariant:") and "currency" in reason
        case _:
            raise AssertionError("expected Err")


def test_load_duplicate_stock_id_returns_invariant_err(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "x",
        """
name: x
stocks:
  - {id: AAA.AS, symbol: AAA, exchange: AS, currency: EUR, isin: x, sector: x, country: x}
  - {id: AAA.AS, symbol: AAA, exchange: AS, currency: EUR, isin: x, sector: x, country: x}
""",
    )
    match load_universe("x", universe_root=tmp_path):
        case Err(reason):
            assert reason.startswith("config:invariant:")
        case _:
            raise AssertionError("expected Err")


def test_load_sorts_stocks_regardless_of_yaml_order(tmp_path: Path) -> None:
    """YAML may list stocks in any order; the loader sorts them
    alphabetically by id so the Universe invariant holds."""
    _write(
        tmp_path,
        "x",
        """
name: x
stocks:
  - {id: ZZZ.AS, symbol: ZZZ, exchange: AS, currency: EUR, isin: x, sector: x, country: x}
  - {id: AAA.AS, symbol: AAA, exchange: AS, currency: EUR, isin: x, sector: x, country: x}
""",
    )
    uni = load_universe("x", universe_root=tmp_path).unwrap()
    assert [str(s.id) for s in uni.stocks] == ["AAA.AS", "ZZZ.AS"]
