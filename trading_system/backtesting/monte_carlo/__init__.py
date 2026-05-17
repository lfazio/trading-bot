"""Monte Carlo simulation (`backtesting/monte_carlo/`) — CR-007.

Composes the existing deterministic backtest engine
(``backtesting/engine.py``) over N synthetic price paths and emits
**summary percentiles only**. v1 ships three generators
(`block_bootstrap`, `gbm`, `regime_stitched`) behind a single
``MCGenerator`` Protocol; the runner injects the backtest factory so
the engine code never moves.

REQ refs:
- REQ_F_MCS_001..006 / REQ_NF_MCS_001 — composition without
  modification; closed v1 generator set; per-path RNG via
  ``sha256(seed||path_index)[:8]``; closed-quintile percentile maps;
  meta-loop integration via 5th-percentile drawdown floor; archive
  joins via ``config_hash`` for CR-008.
- REQ_SDS_MCS_001..004 — L5 placement; per-path RNG seeding formula;
  monotonic percentile maps; single-method Protocol.
- REQ_SDD_MCS_001..006 — import-graph audit; no engine fork; per-path
  RNG determinism; percentile invariants; persistence integration.
"""

from __future__ import annotations

from trading_system.backtesting.monte_carlo.config import (
    GBMParams,
    MCConfig,
    RNGSeed,
)
from trading_system.backtesting.monte_carlo.errors import MonteCarloError
from trading_system.backtesting.monte_carlo.generator import (
    BlockBootstrapGenerator,
    GBMGenerator,
    MCGenerator,
    RegimeStitchedGenerator,
    percentile,
    stddev_decimal,
)
from trading_system.backtesting.monte_carlo.result import (
    QUINTILE_KEYS,
    MonteCarloResult,
)
from trading_system.backtesting.monte_carlo.runner import (
    MonteCarloRunner,
    config_hash,
    seed_for_path,
)

__all__ = [
    "BlockBootstrapGenerator",
    "GBMGenerator",
    "GBMParams",
    "MCConfig",
    "MCGenerator",
    "MonteCarloError",
    "MonteCarloResult",
    "MonteCarloRunner",
    "QUINTILE_KEYS",
    "RegimeStitchedGenerator",
    "RNGSeed",
    "config_hash",
    "percentile",
    "seed_for_path",
    "stddev_decimal",
]
