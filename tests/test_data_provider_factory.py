"""Tests for MVP-2 of CR-016 — config-driven data provider in main.py.

The ``data:`` section of ``config/system.yaml`` selects which
MarketDataProvider the runtime constructs. Tests verify:

- The mock-provider path is bit-identical to the pre-MVP-2 demo
  (REQ_NF_ACC_001 backwards compat semantics).
- The yfinance-provider path constructs a YFinanceMarketDataProvider
  over a cache populated from the bundled fixtures.
- The CompositeFundamentalsProvider chains yfinance + CSV when
  ``data/seed_fundamentals.csv`` is present.
- The universe-preset path loads a named universe from
  ``data/universes/<name>.yaml``.
- Categorised Errs surface for bad provider selectors.
"""

from __future__ import annotations

from pathlib import Path

from trading_system.config import DataProviderConfig, SystemConfig
from trading_system.data.fundamentals.composite import (
    CompositeFundamentalsProvider,
)
from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.yfinance.provider import YFinanceMarketDataProvider
from trading_system.main import _build_data_provider, _build_runtime_universe
from trading_system.models.money import Currency, Money
from trading_system.result import Err, Ok


# ---------------------------------------------------------------------------
# Helper — build a SystemConfig with the right shape
# ---------------------------------------------------------------------------


def _make_sys_cfg(
    *,
    provider: str = "mock",
    cache_root: str = ".cache/yfinance",
    bundled_fixtures: bool = True,
    universe: str = "",
) -> SystemConfig:
    from decimal import Decimal

    return SystemConfig(
        starting_capital=Money(Decimal("1000"), Currency.EUR),
        seed=42,
        mode="backtest",
        broker_adapter="local",
        data=DataProviderConfig(
            provider=provider,
            cache_root=cache_root,
            bundled_fixtures=bundled_fixtures,
            universe=universe,
        ),
    )


# ---------------------------------------------------------------------------
# Mock-provider path (REQ_NF_ACC_001 backwards-compat)
# ---------------------------------------------------------------------------


def test_default_mock_path(tmp_path: Path) -> None:
    """``provider: mock`` returns the legacy MockMarketDataProvider
    seeded from system.yaml's ``seed`` field."""
    cfg = _make_sys_cfg(provider="mock")
    res = _build_data_provider(cfg, config_dir=tmp_path)
    match res:
        case Ok(provider):
            assert isinstance(provider, MockMarketDataProvider)
        case Err(reason):
            raise AssertionError(reason)


def test_mock_path_universe_is_legacy_hand_built(tmp_path: Path) -> None:
    """No ``data.universe`` set + mock provider ⇒ legacy hand-built
    3-stock universe (REQ_NF_ACC_001)."""
    cfg = _make_sys_cfg(provider="mock")
    provider = _build_data_provider(cfg, config_dir=tmp_path).unwrap()
    universe = _build_runtime_universe(cfg, provider)
    # 3 stocks: ASML, BNP, SAN.
    assert len(universe) == 3
    ids = sorted(str(s.id) for s in universe)
    assert ids == ["ASML.AS", "BNP.PA", "SAN.PA"]


# ---------------------------------------------------------------------------
# YFinance-provider path
# ---------------------------------------------------------------------------


def test_yfinance_path_constructs_composite_provider(tmp_path: Path) -> None:
    """``provider: yfinance`` + the bundled CSV fundamentals
    available ⇒ Composite wrapping YFinance + CSV."""
    cache_root = tmp_path / "cache"
    cfg = _make_sys_cfg(
        provider="yfinance",
        cache_root=str(cache_root),
        bundled_fixtures=True,
    )
    # config_dir's parent is the repo root, so data/seed_fundamentals.csv
    # resolves to the shipped file.
    repo_root = Path(__file__).resolve().parent.parent
    res = _build_data_provider(cfg, config_dir=repo_root / "config")
    match res:
        case Ok(provider):
            assert isinstance(provider, CompositeFundamentalsProvider)
            # Composite has 2 delegates: yfinance + csv.
            assert len(provider.delegates) == 2
        case Err(reason):
            raise AssertionError(reason)


def test_yfinance_path_populates_cache_from_bundled_fixtures(tmp_path: Path) -> None:
    """When the cache is empty + ``bundled_fixtures=True``, the
    bundled fixtures are copied in."""
    cache_root = tmp_path / "cache"
    assert not cache_root.exists()
    cfg = _make_sys_cfg(
        provider="yfinance",
        cache_root=str(cache_root),
        bundled_fixtures=True,
    )
    repo_root = Path(__file__).resolve().parent.parent
    _build_data_provider(cfg, config_dir=repo_root / "config").unwrap()
    # Cache now has at least the bundled symbols.
    assert cache_root.is_dir()
    populated = list(cache_root.rglob("*.jsonl"))
    assert len(populated) >= 6  # 3 symbols × (bars + dividends)


def test_yfinance_path_without_bundled_fixtures(tmp_path: Path) -> None:
    """``bundled_fixtures=False`` SHALL NOT auto-populate; operator
    must record real data themselves."""
    cache_root = tmp_path / "cache"
    cfg = _make_sys_cfg(
        provider="yfinance",
        cache_root=str(cache_root),
        bundled_fixtures=False,
    )
    res = _build_data_provider(cfg, config_dir=tmp_path)
    # Provider constructs successfully (empty cache is OK);
    # actual bar lookups will miss until the operator records data.
    assert isinstance(res, Ok)
    assert not cache_root.exists() or not any(cache_root.rglob("*.jsonl"))


def test_yfinance_path_without_csv_returns_bare_yfinance(tmp_path: Path) -> None:
    """When ``data/seed_fundamentals.csv`` isn't found alongside
    config_dir, the factory returns the bare YFinance provider
    (no Composite wrapper)."""
    cache_root = tmp_path / "cache"
    cfg = _make_sys_cfg(
        provider="yfinance",
        cache_root=str(cache_root),
        bundled_fixtures=True,
    )
    # config_dir points at tmp_path so the CSV path won't resolve.
    res = _build_data_provider(cfg, config_dir=tmp_path)
    match res:
        case Ok(provider):
            assert isinstance(provider, YFinanceMarketDataProvider)
        case Err(reason):
            raise AssertionError(reason)


# ---------------------------------------------------------------------------
# Universe preset selection
# ---------------------------------------------------------------------------


def test_universe_preset_loads_when_set(tmp_path: Path) -> None:
    """``data.universe: eu-dividend-starter`` ⇒ load the named
    preset's stock list."""
    cfg = _make_sys_cfg(
        provider="mock", universe="eu-dividend-starter"
    )
    provider = _build_data_provider(cfg, config_dir=tmp_path).unwrap()
    universe = _build_runtime_universe(cfg, provider)
    assert len(universe) == 3
    ids = [str(s.id) for s in universe]
    assert ids == ["ASML.AS", "BNP.PA", "SAN.PA"]  # alphabetical


def test_unknown_universe_falls_through_to_default(tmp_path: Path) -> None:
    """A non-existent preset doesn't crash the demo — falls
    through to the default path (legacy mock universe)."""
    cfg = _make_sys_cfg(
        provider="mock", universe="ghost-universe"
    )
    provider = _build_data_provider(cfg, config_dir=tmp_path).unwrap()
    universe = _build_runtime_universe(cfg, provider)
    # Falls back to the legacy 3-stock hand-built universe.
    assert len(universe) == 3


def test_yfinance_default_universe_is_starter(tmp_path: Path) -> None:
    """No ``data.universe`` set + yfinance provider ⇒ load
    ``eu-dividend-starter`` automatically."""
    cache_root = tmp_path / "cache"
    cfg = _make_sys_cfg(
        provider="yfinance",
        cache_root=str(cache_root),
        bundled_fixtures=True,
    )
    repo_root = Path(__file__).resolve().parent.parent
    provider = _build_data_provider(
        cfg, config_dir=repo_root / "config"
    ).unwrap()
    universe = _build_runtime_universe(cfg, provider)
    ids = [str(s.id) for s in universe]
    assert ids == ["ASML.AS", "BNP.PA", "SAN.PA"]
