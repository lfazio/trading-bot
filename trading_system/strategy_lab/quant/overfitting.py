"""Overfitting-detection pure helpers — REQ_F_QNT_006 / REQ_SDS_QNT_004.

Three independent measurements + one gate:

- ``parameter_to_data_ratio(metrics)`` — ``n_params / n_train_periods``.
  ``Decimal("Infinity")`` when ``n_train_periods <= 0`` (degenerate)
  so the gate trips correctly.
- ``adjusted_sharpe(metrics)`` — degrees-of-freedom Sharpe adjustment:
  ``sharpe / sqrt(1 + 0.5 * (n_params / n_train_periods))``.
- ``information_coefficient(train, oos)`` — Pearson correlation
  between the (sharpe, return, drawdown) vectors of the two metric
  rows. Returns ``Decimal("0")`` when the inputs degenerate (zero
  variance in either vector).
- ``overfitting_gate(metrics, *, ratio_max, ic_floor)`` — categorised
  ``Result[None, str]`` reject when either threshold trips.

The functions take a ``StrategyMetrics`` (extended with three new
fields — see ``trading_system/strategy_lab/metrics.py``).
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.result import Err, Ok, Result
from trading_system.strategy_lab.metrics import StrategyMetrics


# Default thresholds — overrideable per-call.
DEFAULT_RATIO_MAX: Decimal = Decimal("0.10")
DEFAULT_IC_FLOOR: Decimal = Decimal("0.30")


def parameter_to_data_ratio(metrics: StrategyMetrics) -> Decimal:
    """Returns ``n_params / n_train_periods``.

    Returns ``Decimal("Infinity")`` when ``n_train_periods <= 0``
    so the gate rejects the degenerate case as severely overfit
    rather than silently passing.
    """
    if metrics.n_train_periods <= 0:
        return Decimal("Infinity")
    return Decimal(metrics.n_params) / Decimal(metrics.n_train_periods)


def adjusted_sharpe(metrics: StrategyMetrics) -> Decimal:
    """Sharpe adjusted for degrees-of-freedom.

    Formula: ``sharpe / sqrt(1 + 0.5 * parameter_to_data_ratio)``.
    Returns ``Decimal("0")`` if the raw Sharpe is zero, regardless
    of the ratio.
    """
    if metrics.sharpe == 0:
        return Decimal("0")
    ratio = parameter_to_data_ratio(metrics)
    if ratio == Decimal("Infinity"):
        # Degenerate denominator — return 0 so consumers see "do not
        # ship" rather than NaN.
        return Decimal("0")
    denom_sq = Decimal(1) + Decimal("0.5") * ratio
    if denom_sq <= 0:
        return Decimal("0")
    return metrics.sharpe / denom_sq.sqrt()


def information_coefficient(
    train: StrategyMetrics, oos: StrategyMetrics
) -> Decimal:
    """Pearson IC between the train + OOS metric vectors.

    The "metric vector" is the closed 3-tuple
    ``(sharpe, net_after_tax_return, max_drawdown)``. Returns
    ``Decimal("0")`` when either vector has zero variance (one or
    both metric values are identical across the triple) — that's
    the degenerate input the meta-loop should treat as "no signal".
    """
    train_vec = (
        train.sharpe,
        train.net_after_tax_return,
        train.max_drawdown,
    )
    oos_vec = (oos.sharpe, oos.net_after_tax_return, oos.max_drawdown)
    return _pearson(train_vec, oos_vec)


def overfitting_gate(
    metrics: StrategyMetrics,
    *,
    ratio_max: Decimal = DEFAULT_RATIO_MAX,
    ic_floor: Decimal = DEFAULT_IC_FLOOR,
) -> Result[None, str]:
    """Reject when either threshold trips.

    Categorised Errs:
        overfitting:parameter_to_data_ratio:<ratio_max>
        overfitting:low_information_coefficient:<ic_floor>
    """
    ratio = parameter_to_data_ratio(metrics)
    if ratio > ratio_max:
        return Err(f"overfitting:parameter_to_data_ratio:{ratio_max}")
    if metrics.information_coefficient < ic_floor:
        return Err(f"overfitting:low_information_coefficient:{ic_floor}")
    return Ok(None)


# ---------------------------------------------------------------------------
# Pearson correlation — Decimal-precise; no float intermediates
# ---------------------------------------------------------------------------


def _pearson(a: tuple[Decimal, ...], b: tuple[Decimal, ...]) -> Decimal:
    if len(a) != len(b):
        raise ValueError(
            f"_pearson: vector length mismatch {len(a)} != {len(b)}"
        )
    n = Decimal(len(a))
    if n == 0:
        return Decimal("0")
    mean_a = sum(a, start=Decimal(0)) / n
    mean_b = sum(b, start=Decimal(0)) / n
    var_a = sum(((x - mean_a) * (x - mean_a) for x in a), start=Decimal(0))
    var_b = sum(((y - mean_b) * (y - mean_b) for y in b), start=Decimal(0))
    if var_a == 0 or var_b == 0:
        return Decimal("0")
    cov = sum(
        ((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True)),
        start=Decimal(0),
    )
    return cov / (var_a.sqrt() * var_b.sqrt())
