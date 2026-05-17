"""``MonteCarloResult`` frozen aggregate with percentile-keyset +
monotonicity invariants.

REQ refs:
- REQ_F_MCS_004 — closed quintile keyset, monotonic percentile maps,
  KS trip rate, n_paths, config_hash for CR-008 join.
- REQ_SDD_MCS_004 — constructor panics with
  ``mc:percentile_invariant:<field>:<reason>`` on key-set or
  monotonicity violation; ``kill_switch_trip_rate`` constrained to
  ``[0, 1]``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

QUINTILE_KEYS: tuple[Decimal, ...] = (
    Decimal("0.05"),
    Decimal("0.25"),
    Decimal("0.50"),
    Decimal("0.75"),
    Decimal("0.95"),
)
"""Closed quintile keyset. Use this tuple, not a private duplicate."""


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    """Outcome of a single Monte Carlo run — summary percentiles only.

    Per-path histories are deliberately out-of-scope in v1; the archive
    keyed on ``(strategy_sha, config_hash, seed, n_paths)`` reconstructs
    the path set on demand if a reviewer needs to drill in.

    Drawdown percentiles are stored as positive decimals so the same
    monotonicity check (P5 ≤ P25 ≤ ... ≤ P95) applies uniformly across
    the three percentile maps.
    """

    equity_percentiles: Mapping[Decimal, Decimal]
    drawdown_percentiles: Mapping[Decimal, Decimal]
    sharpe_percentiles: Mapping[Decimal, Decimal]
    kill_switch_trip_rate: Decimal
    n_paths: int
    config_hash: str

    def __post_init__(self) -> None:
        for name in ("equity_percentiles", "drawdown_percentiles", "sharpe_percentiles"):
            m = getattr(self, name)
            if set(m.keys()) != set(QUINTILE_KEYS):
                raise RuntimeError(
                    f"mc:percentile_invariant:{name}:keyset"
                )
            vals = [m[k] for k in QUINTILE_KEYS]
            if vals != sorted(vals):
                raise RuntimeError(
                    f"mc:percentile_invariant:{name}:monotonicity"
                )
        if not (Decimal("0") <= self.kill_switch_trip_rate <= Decimal("1")):
            raise RuntimeError(
                f"mc:percentile_invariant:kill_switch_trip_rate:out_of_bounds: "
                f"{self.kill_switch_trip_rate}"
            )
        if self.n_paths <= 0:
            raise RuntimeError(
                f"mc:percentile_invariant:n_paths:non_positive: {self.n_paths}"
            )
        if not self.config_hash:
            raise RuntimeError("mc:percentile_invariant:config_hash:empty")
