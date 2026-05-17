"""``MCConfig`` + ``GBMParams`` + ``RNGSeed`` for the Monte Carlo runner.

REQ refs:
- REQ_F_MCS_003 — closed validator set; categorised
  ``mc:config_mismatch:<field>`` Errs for generator/field cross-checks.
- REQ_SDD_MCS_005 — generator/field cross-check formula.
- REQ_SDS_MCS_002 — ``RNGSeed`` NewType.

The ``MCConfig.__post_init__`` is the single boundary that translates
operator misconfiguration into a categorised ``ValueError`` — the
runner never re-checks these invariants downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, NewType

RNGSeed = NewType("RNGSeed", int)


@dataclass(frozen=True, slots=True)
class GBMParams:
    """Geometric Brownian motion parameters: ``r_t = mu + sigma * z_t``."""

    mu: Decimal
    sigma: Decimal

    def __post_init__(self) -> None:
        if self.sigma < 0:
            raise ValueError(
                f"mc:config_mismatch:gbm_params: sigma must be >= 0, got {self.sigma}"
            )


@dataclass(frozen=True, slots=True)
class MCConfig:
    """Monte Carlo run configuration.

    ``generator`` selects one of the three v1 generators:
      - ``"block_bootstrap"`` — requires ``block_length``.
      - ``"gbm"`` — requires ``gbm_params``.
      - ``"regime_stitched"`` — requires ``regime_window``.

    Other generator-specific fields SHALL be ``None``; setting one that
    doesn't match the chosen generator is permitted (it is simply ignored)
    so callers may carry shared defaults without per-generator branching.
    """

    generator: Literal["block_bootstrap", "gbm", "regime_stitched"]
    n_paths: int
    seed: RNGSeed
    block_length: int | None = None
    gbm_params: GBMParams | None = None
    regime_window: int | None = None

    def __post_init__(self) -> None:
        if not (100 <= self.n_paths <= 100_000):
            raise ValueError(
                f"mc:n_paths_out_of_bounds: MCConfig.n_paths must be in "
                f"[100, 100_000], got {self.n_paths}"
            )
        if self.generator == "block_bootstrap" and self.block_length is None:
            raise ValueError("mc:config_mismatch:block_length")
        if self.generator == "gbm" and self.gbm_params is None:
            raise ValueError("mc:config_mismatch:gbm_params")
        if self.generator == "regime_stitched" and self.regime_window is None:
            raise ValueError("mc:config_mismatch:regime_window")
        if self.block_length is not None and self.block_length <= 0:
            raise ValueError(
                f"mc:bad_block_length: block_length must be > 0, "
                f"got {self.block_length}"
            )
        if self.regime_window is not None and self.regime_window <= 0:
            raise ValueError(
                f"mc:config_mismatch:regime_window: must be > 0, "
                f"got {self.regime_window}"
            )
