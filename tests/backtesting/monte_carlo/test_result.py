"""TC_MCS_007 — ``MonteCarloResult`` invariants.

REQ refs:
- REQ_F_MCS_004 — closed quintile keyset, monotonic percentile maps,
  KS trip rate, n_paths, config_hash.
- REQ_SDS_MCS_003 — ``MonteCarloResult`` carries percentile maps with
  the closed quintile keyset + monotonicity invariant. This test
  exercises both the keyset check and the monotonicity check.
- REQ_SDD_MCS_004 — constructor panics with
  ``mc:percentile_invariant:<field>:<reason>`` on invariant violation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.backtesting.monte_carlo import (
    QUINTILE_KEYS,
    MonteCarloResult,
)


def _valid_pct_map() -> dict[Decimal, Decimal]:
    return {k: k * Decimal("100") for k in QUINTILE_KEYS}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_construction_round_trips() -> None:
    r = MonteCarloResult(
        equity_percentiles=_valid_pct_map(),
        drawdown_percentiles=_valid_pct_map(),
        sharpe_percentiles=_valid_pct_map(),
        kill_switch_trip_rate=Decimal("0.05"),
        n_paths=500,
        config_hash="abc123",
    )
    assert r.n_paths == 500
    assert r.config_hash == "abc123"


# ---------------------------------------------------------------------------
# Invariants — TC_MCS_007
# ---------------------------------------------------------------------------


def test_extra_percentile_key_panics() -> None:
    bad = dict(_valid_pct_map())
    bad[Decimal("0.99")] = Decimal("99")
    with pytest.raises(RuntimeError, match="mc:percentile_invariant:equity_percentiles:keyset"):
        MonteCarloResult(
            equity_percentiles=bad,
            drawdown_percentiles=_valid_pct_map(),
            sharpe_percentiles=_valid_pct_map(),
            kill_switch_trip_rate=Decimal("0"),
            n_paths=100,
            config_hash="x",
        )


def test_missing_percentile_key_panics() -> None:
    bad = dict(_valid_pct_map())
    bad.pop(Decimal("0.95"))
    with pytest.raises(RuntimeError, match="keyset"):
        MonteCarloResult(
            equity_percentiles=bad,
            drawdown_percentiles=_valid_pct_map(),
            sharpe_percentiles=_valid_pct_map(),
            kill_switch_trip_rate=Decimal("0"),
            n_paths=100,
            config_hash="x",
        )


def test_non_monotonic_drawdown_panics() -> None:
    bad = {k: Decimal("1") for k in QUINTILE_KEYS}
    bad[Decimal("0.05")] = Decimal("5")  # > 0.25's value of 1
    with pytest.raises(RuntimeError, match="drawdown_percentiles:monotonicity"):
        MonteCarloResult(
            equity_percentiles=_valid_pct_map(),
            drawdown_percentiles=bad,
            sharpe_percentiles=_valid_pct_map(),
            kill_switch_trip_rate=Decimal("0"),
            n_paths=100,
            config_hash="x",
        )


def test_ks_trip_rate_out_of_bounds_panics() -> None:
    with pytest.raises(RuntimeError, match="kill_switch_trip_rate:out_of_bounds"):
        MonteCarloResult(
            equity_percentiles=_valid_pct_map(),
            drawdown_percentiles=_valid_pct_map(),
            sharpe_percentiles=_valid_pct_map(),
            kill_switch_trip_rate=Decimal("1.5"),
            n_paths=100,
            config_hash="x",
        )


def test_negative_ks_trip_rate_panics() -> None:
    with pytest.raises(RuntimeError, match="kill_switch_trip_rate"):
        MonteCarloResult(
            equity_percentiles=_valid_pct_map(),
            drawdown_percentiles=_valid_pct_map(),
            sharpe_percentiles=_valid_pct_map(),
            kill_switch_trip_rate=Decimal("-0.1"),
            n_paths=100,
            config_hash="x",
        )


def test_zero_n_paths_panics() -> None:
    with pytest.raises(RuntimeError, match="n_paths"):
        MonteCarloResult(
            equity_percentiles=_valid_pct_map(),
            drawdown_percentiles=_valid_pct_map(),
            sharpe_percentiles=_valid_pct_map(),
            kill_switch_trip_rate=Decimal("0"),
            n_paths=0,
            config_hash="x",
        )


def test_empty_config_hash_panics() -> None:
    with pytest.raises(RuntimeError, match="config_hash:empty"):
        MonteCarloResult(
            equity_percentiles=_valid_pct_map(),
            drawdown_percentiles=_valid_pct_map(),
            sharpe_percentiles=_valid_pct_map(),
            kill_switch_trip_rate=Decimal("0"),
            n_paths=100,
            config_hash="",
        )
