"""TC_MCS_002 (per-path RNG determinism) + runner aggregation +
``config_hash`` integration.

REQ refs:
- REQ_NF_MCS_001 — bit-identical replay determinism.
- REQ_F_MCS_001 — composition without engine modification.
- REQ_F_MCS_004 — percentile-map shape.
- REQ_F_MCS_006 — archive-key tuple (config_hash + seed + n_paths).
- REQ_SDD_MCS_006 — ``config_hash = sha256(canonical_json(MCConfig))``
  is the join key the CR-008 ``MonteCarloResultRepository`` archives
  against; this test verifies the formula's stability + that the hash
  changes when any field that affects replay changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.backtesting.monte_carlo import (
    GBMParams,
    MCConfig,
    MonteCarloResult,
    MonteCarloRunner,
    QUINTILE_KEYS,
    RNGSeed,
    config_hash,
)
from trading_system.backtesting.result import BacktestResult
from trading_system.data.types import Bar
from trading_system.models.flow import EquityPoint
from trading_system.models.money import Currency, Money


_START = datetime(2024, 1, 2, tzinfo=UTC)


def _bars(closes: list[str]) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            at=_START + timedelta(days=i),
            open=Decimal(c),
            high=Decimal(c),
            low=Decimal(c),
            close=Decimal(c),
            volume=Decimal("1"),
        )
        for i, c in enumerate(closes)
    )


def _make_equity_curve(final: Decimal, max_dd: Decimal) -> tuple[EquityPoint, ...]:
    return (
        EquityPoint(
            at=_START,
            equity_gross=Money(Decimal("1000"), Currency.EUR),
            equity_after_tax=Money(Decimal("1000"), Currency.EUR),
            drawdown_pct=Decimal("0"),
        ),
        EquityPoint(
            at=_START + timedelta(days=1),
            equity_gross=Money(final, Currency.EUR),
            equity_after_tax=Money(final, Currency.EUR),
            drawdown_pct=max_dd,
        ),
    )


@dataclass(slots=True)
class _DeterministicBacktest:
    """Stub Backtest that materialises a BacktestResult deterministically
    from the path's closing prices — no engine touched."""

    bars: tuple[Bar, ...]

    def run(self) -> BacktestResult:
        first_close = self.bars[0].close
        last_close = self.bars[-1].close
        peak = max(b.close for b in self.bars)
        drawdown = (peak - last_close) / peak if peak > 0 else Decimal("0")
        final_equity = Decimal("1000") * (last_close / first_close)
        curve = _make_equity_curve(final_equity, drawdown.quantize(Decimal("0.000001")))
        return BacktestResult(
            trades=(),
            equity_curve=curve,
            equity_excl_injections=tuple(p.equity_after_tax.amount for p in curve),
            final_cash=Money(final_equity, Currency.EUR),
            final_equity_after_tax=Money(final_equity, Currency.EUR),
            realized_gross=Money(Decimal("0"), Currency.EUR),
            realized_after_tax=Money(Decimal("0"), Currency.EUR),
            dividends_gross=Money(Decimal("0"), Currency.EUR),
            dividends_after_tax=Money(Decimal("0"), Currency.EUR),
            knockouts=0,
            injections_applied=0,
        )


def _factory() -> Callable[[object, tuple[Bar, ...]], _DeterministicBacktest]:
    def factory(_strategy: object, bars: tuple[Bar, ...]) -> _DeterministicBacktest:
        return _DeterministicBacktest(bars=bars)

    return factory


# ---------------------------------------------------------------------------
# TC_MCS_002 — determinism
# ---------------------------------------------------------------------------


def test_runner_two_runs_produce_equal_result() -> None:
    bars = _bars([f"{100 + (i % 20)}" for i in range(120)])
    cfg = MCConfig(
        generator="gbm",
        n_paths=100,
        seed=RNGSeed(42),
        gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.02")),
    )
    runner = MonteCarloRunner(backtest_factory=_factory())
    a = runner.run(strategy=object(), historical_bars=bars, mc_config=cfg).unwrap()
    b = runner.run(strategy=object(), historical_bars=bars, mc_config=cfg).unwrap()
    assert a == b


def test_runner_different_seed_produces_different_result() -> None:
    bars = _bars([f"{100 + (i % 20)}" for i in range(120)])
    cfg_a = MCConfig(
        generator="gbm",
        n_paths=100,
        seed=RNGSeed(42),
        gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.02")),
    )
    cfg_b = MCConfig(
        generator="gbm",
        n_paths=100,
        seed=RNGSeed(43),
        gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.02")),
    )
    runner = MonteCarloRunner(backtest_factory=_factory())
    a = runner.run(strategy=object(), historical_bars=bars, mc_config=cfg_a).unwrap()
    b = runner.run(strategy=object(), historical_bars=bars, mc_config=cfg_b).unwrap()
    assert a != b


# ---------------------------------------------------------------------------
# Aggregate shape
# ---------------------------------------------------------------------------


def test_runner_emits_full_percentile_maps() -> None:
    bars = _bars([f"{100 + (i % 10)}" for i in range(100)])
    cfg = MCConfig(
        generator="block_bootstrap",
        n_paths=100,
        seed=RNGSeed(7),
        block_length=10,
    )
    runner = MonteCarloRunner(backtest_factory=_factory())
    result = runner.run(strategy=object(), historical_bars=bars, mc_config=cfg).unwrap()
    assert isinstance(result, MonteCarloResult)
    # All three percentile maps SHALL carry the full closed quintile set.
    assert set(result.equity_percentiles.keys()) == set(QUINTILE_KEYS)
    assert set(result.drawdown_percentiles.keys()) == set(QUINTILE_KEYS)
    assert set(result.sharpe_percentiles.keys()) == set(QUINTILE_KEYS)
    assert result.n_paths == 100
    # KS trip rate in [0, 1]; our stub never trips (knockouts==0; drawdowns small).
    assert Decimal("0") <= result.kill_switch_trip_rate <= Decimal("1")


def test_runner_n_paths_matches_config() -> None:
    bars = _bars([f"{100 + (i % 5)}" for i in range(50)])
    cfg = MCConfig(
        generator="block_bootstrap",
        n_paths=250,
        seed=RNGSeed(1),
        block_length=5,
    )
    runner = MonteCarloRunner(backtest_factory=_factory())
    result = runner.run(strategy=object(), historical_bars=bars, mc_config=cfg).unwrap()
    assert result.n_paths == 250


# ---------------------------------------------------------------------------
# config_hash
# ---------------------------------------------------------------------------


def test_config_hash_stable_across_calls() -> None:
    cfg = MCConfig(
        generator="gbm",
        n_paths=100,
        seed=RNGSeed(11),
        gbm_params=GBMParams(mu=Decimal("0.001"), sigma=Decimal("0.02")),
    )
    assert config_hash(cfg) == config_hash(cfg)
    # Hex SHA-256.
    assert len(config_hash(cfg)) == 64


def test_config_hash_changes_when_seed_changes() -> None:
    base = MCConfig(
        generator="gbm",
        n_paths=100,
        seed=RNGSeed(11),
        gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.01")),
    )
    bumped = MCConfig(
        generator="gbm",
        n_paths=100,
        seed=RNGSeed(12),
        gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.01")),
    )
    assert config_hash(base) != config_hash(bumped)


def test_config_hash_changes_when_generator_changes() -> None:
    a = MCConfig(
        generator="block_bootstrap",
        n_paths=100,
        seed=RNGSeed(1),
        block_length=10,
    )
    b = MCConfig(
        generator="gbm",
        n_paths=100,
        seed=RNGSeed(1),
        gbm_params=GBMParams(mu=Decimal("0"), sigma=Decimal("0.01")),
    )
    assert config_hash(a) != config_hash(b)
