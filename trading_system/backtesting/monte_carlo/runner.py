"""``MonteCarloRunner`` — composes the existing deterministic backtest
engine over N synthetic paths and emits summary percentiles only.

REQ refs:
- REQ_F_MCS_001 — composes ``backtesting/engine.py`` without modification
  via the injected ``backtest_factory`` callable.
- REQ_F_MCS_004 — percentile maps (P5/P25/P50/P75/P95) for equity /
  drawdown / sharpe + KS trip rate + config_hash for CR-008 join.
- REQ_NF_MCS_001 — bit-identical replay: identical
  ``(strategy, historical_bars, MCConfig)`` ⇒ identical
  ``MonteCarloResult`` rows.
- REQ_SDS_MCS_002 / REQ_SDD_MCS_003 — per-path seed via
  ``sha256(seed||path_index)[:8]``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from trading_system.backtesting.monte_carlo.config import MCConfig
from trading_system.backtesting.monte_carlo.errors import MonteCarloError
from trading_system.backtesting.monte_carlo.generator import (
    BlockBootstrapGenerator,
    GBMGenerator,
    MCGenerator,
    RegimeStitchedGenerator,
    percentile,
)
from trading_system.backtesting.monte_carlo.result import (
    QUINTILE_KEYS,
    MonteCarloResult,
)
from trading_system.backtesting.result import BacktestResult
from trading_system.backtesting.walk_forward import sharpe_ratio
from trading_system.data.types import Bar
from trading_system.result import Err, Ok, Result


def seed_for_path(seed: int, path_index: int) -> int:
    """REQ_SDS_MCS_002 — derive per-path seed via SHA-256.

    ``int.from_bytes(sha256(int(seed) || path_index)[:8], "big")``. Two
    parallel callers passing the same ``(seed, path_index)`` see the
    same 64-bit derived seed. Big-endian byte ordering is the
    canonical form (TC_MCS_003 fails if this changes).
    """
    payload = int(seed).to_bytes(8, "big") + int(path_index).to_bytes(8, "big")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def config_hash(mc_config: MCConfig) -> str:
    """SHA-256 of a canonical-JSON form of ``MCConfig``.

    Used as the join key for the CR-008 ``MonteCarloResultRepository``
    archive (REQ_F_MCS_006 / REQ_SDD_MCS_006). Decimal fields serialise
    as strings; the keyset is sorted to make the form canonical.
    """
    payload: dict[str, Any] = {
        "generator": mc_config.generator,
        "n_paths": mc_config.n_paths,
        "seed": int(mc_config.seed),
        "block_length": mc_config.block_length,
        "regime_window": mc_config.regime_window,
    }
    if mc_config.gbm_params is not None:
        payload["gbm_params"] = {
            "mu": str(mc_config.gbm_params.mu),
            "sigma": str(mc_config.gbm_params.sigma),
        }
    else:
        payload["gbm_params"] = None
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class MonteCarloRunner:
    """Owns per-path seeding + composition; never modifies
    ``backtesting.engine``.

    ``backtest_factory`` is a closure the caller supplies that captures
    every non-bars input (strategies, instruments, fee/slippage/risk
    models, phase constraints, regime, screener ranking, broker config
    …) and returns a freshly-assembled ``Backtest`` for the given
    path's strategy + synthetic bars. The runner's only contract with
    the engine is ``factory(...).run() -> BacktestResult``.

    ``regime_detector`` is optional; ``RegimeStitchedGenerator`` uses it
    if present, otherwise falls back to a single-regime bootstrap.
    """

    backtest_factory: Callable[[Any, tuple[Bar, ...]], Any]
    regime_detector: object | None = None
    _generators: dict[str, MCGenerator] = field(default_factory=dict, init=False)

    def run(
        self,
        strategy: Any,
        historical_bars: tuple[Bar, ...],
        mc_config: MCConfig,
    ) -> Result[MonteCarloResult, MonteCarloError]:
        generator_or_err = self._make_generator(mc_config)
        if isinstance(generator_or_err, Err):
            return generator_or_err
        gen = generator_or_err.value

        per_path_results: list[BacktestResult] = []
        for i in range(mc_config.n_paths):
            derived_seed = seed_for_path(int(mc_config.seed), i)
            path_or_err = gen.generate(
                historical_bars, seed=derived_seed, n_steps=len(historical_bars)
            )
            if isinstance(path_or_err, Err):
                return Err(
                    MonteCarloError(
                        f"mc:generator_failed:{path_or_err.error.category}",
                        path_or_err.error.detail,
                    )
                )
            backtest = self.backtest_factory(strategy, path_or_err.value)
            per_path_results.append(backtest.run())

        return Ok(self._aggregate(per_path_results, mc_config))

    def _make_generator(
        self, mc_config: MCConfig
    ) -> Result[MCGenerator, MonteCarloError]:
        match mc_config.generator:
            case "block_bootstrap":
                assert mc_config.block_length is not None  # invariant: MCConfig validator
                return Ok(BlockBootstrapGenerator(block_length=mc_config.block_length))
            case "gbm":
                assert mc_config.gbm_params is not None
                return Ok(GBMGenerator(gbm_params=mc_config.gbm_params))
            case "regime_stitched":
                assert mc_config.regime_window is not None
                return Ok(
                    RegimeStitchedGenerator(
                        regime_window=mc_config.regime_window,
                        detector=self.regime_detector,
                    )
                )
        return Err(
            MonteCarloError(f"mc:config_mismatch:generator", mc_config.generator)
        )

    def _aggregate(
        self,
        results: list[BacktestResult],
        mc_config: MCConfig,
    ) -> MonteCarloResult:
        final_equity = [r.final_equity_after_tax.amount for r in results]
        max_drawdowns = [self._max_drawdown(r) for r in results]
        sharpes = [
            sharpe_ratio(r.equity_curve) if r.equity_curve else Decimal("0")
            for r in results
        ]
        trips = sum(1 for r in results if self._did_ks_trip(r))

        return MonteCarloResult(
            equity_percentiles={k: percentile(final_equity, k) for k in QUINTILE_KEYS},
            drawdown_percentiles={k: percentile(max_drawdowns, k) for k in QUINTILE_KEYS},
            sharpe_percentiles={k: percentile(sharpes, k) for k in QUINTILE_KEYS},
            kill_switch_trip_rate=Decimal(trips) / Decimal(len(results))
            if results
            else Decimal("0"),
            n_paths=len(results),
            config_hash=config_hash(mc_config),
        )

    @staticmethod
    def _max_drawdown(result: BacktestResult) -> Decimal:
        if not result.equity_curve:
            return Decimal("0")
        peak = max(result.equity_curve, key=lambda p: p.drawdown_pct).drawdown_pct
        return peak

    @staticmethod
    def _did_ks_trip(result: BacktestResult) -> bool:
        """v1 KS-trip proxy — knockouts trip the safety layer in the
        existing engine, and any drawdown that crossed 25% (a
        Phase-1..3 ceiling) is the most-likely path that would have
        tripped a financial trigger. The CR-007 Phase-B follow-up
        threads the actual ``KillSwitchState`` snapshot through
        ``BacktestResult`` so this proxy becomes a direct read."""
        if result.knockouts > 0:
            return True
        if not result.equity_curve:
            return False
        peak_dd = max(p.drawdown_pct for p in result.equity_curve)
        return peak_dd >= Decimal("0.25")
