"""Quant hypothesis layer — CR-002 Phase 6 (offline-only).

The layer bridges Claude's qualitative reasoning and Python's
quantitative validation:

  operator question
    -> Claude proposes Hypothesis (claim + falsification criterion)
    -> HypothesisValidator (5 gates: structural / bounds / falsifiable /
       metric-aligned / dataset-sane) catches hallucinations BEFORE
       backtest
    -> HypothesisLibrary marks PENDING
    -> backtester (deterministic Python) runs the empirical test
    -> evaluator applies REQ_F_QNT_006 overfitting gates
    -> library transitions PENDING → VALIDATED / REJECTED
    -> ImprovementReport links every shipped strategy to the
       hypothesis_ids that justified it (REQ_F_QNT_005)

Per REQ_NF_QNT_001 the package SHALL be **offline-only** — no
runtime module SHALL import ``trading_system.strategy_lab.quant``.
The import-graph audit at ``tests/strategy_lab/quant/test_structural.py``
enforces this.

REQ refs: REQ_F_QNT_001..006, REQ_NF_QNT_001..002, REQ_SDS_QNT_001..004,
REQ_SDD_QNT_001..008.
"""

from __future__ import annotations

from trading_system.strategy_lab.quant.hypothesis import (
    DEFAULT_METRIC_VOCABULARY,
    DatasetWindow,
    Direction,
    Hypothesis,
    HypothesisId,
    HypothesisResult,
    HypothesisState,
)
from trading_system.strategy_lab.quant.library import (
    HypothesisLibrary,
    InMemoryHypothesisStore,
)
from trading_system.strategy_lab.quant.loader import (
    QuantConfig,
    load_quant_config,
)
from trading_system.strategy_lab.quant.overfitting import (
    OverfittingConfig,
    adjusted_sharpe,
    information_coefficient,
    overfitting_gate,
    parameter_to_data_ratio,
)
from trading_system.strategy_lab.quant.runner import (
    BacktesterAdapter,
    EvaluatorAdapter,
    HypothesisRunner,
)
from trading_system.strategy_lab.quant.validator import (
    HypothesisValidator,
    ValidatorConfig,
)

__all__ = [
    "DEFAULT_METRIC_VOCABULARY",
    "BacktesterAdapter",
    "DatasetWindow",
    "Direction",
    "EvaluatorAdapter",
    "Hypothesis",
    "HypothesisId",
    "HypothesisLibrary",
    "HypothesisResult",
    "HypothesisRunner",
    "HypothesisState",
    "HypothesisValidator",
    "InMemoryHypothesisStore",
    "OverfittingConfig",
    "QuantConfig",
    "ValidatorConfig",
    "adjusted_sharpe",
    "information_coefficient",
    "load_quant_config",
    "overfitting_gate",
    "parameter_to_data_ratio",
]
