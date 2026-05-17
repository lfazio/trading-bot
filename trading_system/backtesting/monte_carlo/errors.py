"""Closed ``MonteCarloError`` category set for the runner + generators.

REQ refs: REQ_F_MCS_003, REQ_SDD_MCS_005 (categorised Errs).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MonteCarloError:
    """Categorised error for the Monte Carlo subsystem.

    ``category`` SHALL be one of:
      - ``mc:config_mismatch:<field>`` — generator/field cross-check failed.
      - ``mc:n_paths_out_of_bounds`` — ``n_paths`` outside ``[100, 100_000]``.
      - ``mc:bad_block_length`` — ``block_length <= 0`` or larger than series.
      - ``mc:gbm_params_missing`` — ``gbm`` generator without ``gbm_params``.
      - ``mc:regime_window_missing`` — ``regime_stitched`` without ``regime_window``.
      - ``mc:generator_failed:<reason>`` — generator surfaced an Err mid-run.
      - ``mc:empty_history`` — ``historical_bars`` was empty.
    """

    category: str
    detail: str = ""
