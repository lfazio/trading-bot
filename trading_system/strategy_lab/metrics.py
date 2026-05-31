"""``StrategyMetrics`` — the metric vector consumed by the meta-loop.

Every consumer in the loop reads from this single struct:

- **score** (REQ_F_MTO_003) reads ``net_after_tax_return``, ``sharpe``,
  ``stability``, ``dd_penalty`` (weights 0.4 / 0.3 / 0.2 / 0.1).
- **risk_guard** reads ``max_drawdown``, ``turnover``,
  ``regime_stability``, ``leverage``, ``parameter_sensitivity``.
- **optimizer** (safe-self-improvement, REQ_F_MTO_006) reads ``risk``
  and ``return_`` to enforce
  ``new_risk <= baseline_risk AND new_return/risk > baseline``.

The fields are intentionally redundant where the SDD §7 pseudo-code
referenced separate names: ``net_after_tax_return`` and ``return_``
carry the same value, but the optimizer's safe-improvement rule
explicitly compares ``return_/risk`` against the baseline so the
field is kept distinct for documentation and cross-cycle tooling
that may want to swap in a different "return" denominator
(e.g., gross). ``dd_penalty`` is derived from ``max_drawdown``;
the evaluator computes it once at construction.

REQ refs: REQ_F_MTO_003, REQ_F_MTO_005, REQ_F_MTO_006, REQ_F_MTO_008,
REQ_SDS_CRS_003.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class StrategyMetrics:
    """Frozen metric vector for a single strategy candidate."""

    # ----- Score inputs (REQ_F_MTO_003) ---------------------------------
    net_after_tax_return: Decimal
    sharpe: Decimal
    stability: Decimal  # in [0, 1]
    dd_penalty: Decimal  # in [0, 1]; bigger drawdown -> bigger penalty

    # ----- Risk-guard inputs --------------------------------------------
    max_drawdown: Decimal  # in [0, 1]
    turnover: Decimal  # absolute count or rate; non-negative
    regime_stability: Decimal  # in [0, 1]; failure in any regime -> low
    leverage: Decimal  # peak observed; >= 1.0 means levered
    parameter_sensitivity: Decimal  # in [0, 1]; lower = more robust

    # ----- Optimizer inputs (REQ_F_MTO_006) ------------------------------
    risk: Decimal  # canonical risk; typically annualized vol
    return_: Decimal  # alias of net_after_tax_return for clarity

    # ----- Overfitting inputs (REQ_F_QNT_006, CR-002) --------------------
    # Defaults make these backwards-compatible — existing callers that
    # don't yet supply the fields get a "no overfitting info" row
    # (n_params=0 + n_train_periods=0 ⇒ parameter_to_data_ratio is
    # Decimal("Infinity"), which the overfitting gate rejects — that's
    # correct: a candidate WITHOUT measured parameter count should not
    # ship through a strict-overfitting gate).
    n_params: int = 0  # count of free parameters
    n_train_periods: int = 0  # sample size of the training window
    information_coefficient: Decimal = Decimal("0")  # Pearson IC train ↔ OOS

    # ----- CR-028 indicator signals --------------------------------------
    # Optional readings populated by strategies that consume the CR-028
    # technical-indicator library at decision time. CR-015's
    # ``TradeRationale.signal_reason`` SHALL consume these so the audit
    # trail records the indicator state at the moment the strategy
    # decided. ``None`` ⇒ the strategy did not consume the indicator
    # (or didn't have enough warm-up history yet — REQ_F_IND_002).
    sma_200_signal: Decimal | None = None
    rsi_signal: Decimal | None = None
    atr_signal: Decimal | None = None
    obv_signal: Decimal | None = None
    adx_signal: Decimal | None = None
    vix_signal: Decimal | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("stability", self.stability),
            ("dd_penalty", self.dd_penalty),
            ("max_drawdown", self.max_drawdown),
            ("regime_stability", self.regime_stability),
            ("parameter_sensitivity", self.parameter_sensitivity),
        ):
            if not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"StrategyMetrics.{name} must lie in [0, 1], got {value}")
        if self.turnover < 0:
            raise ValueError(f"StrategyMetrics.turnover must be >= 0, got {self.turnover}")
        if self.leverage < 0:
            raise ValueError(f"StrategyMetrics.leverage must be >= 0, got {self.leverage}")
        if self.risk < 0:
            raise ValueError(f"StrategyMetrics.risk must be >= 0, got {self.risk}")
        if self.n_params < 0:
            raise ValueError(
                f"StrategyMetrics.n_params must be >= 0, got {self.n_params}"
            )
        if self.n_train_periods < 0:
            raise ValueError(
                f"StrategyMetrics.n_train_periods must be >= 0, "
                f"got {self.n_train_periods}"
            )
        if not (Decimal("-1") <= self.information_coefficient <= Decimal("1")):
            raise ValueError(
                f"StrategyMetrics.information_coefficient must lie in "
                f"[-1, 1], got {self.information_coefficient}"
            )

    def to_signal_reason(self) -> str:
        """Render the indicator readings as a canonical
        ``TradeRationale.signal_reason`` string.

        Format: ``"name=value;name=value;..."`` sorted by
        indicator name; ``None``-valued indicators SHALL be
        omitted (the strategy did not consume them or warm-up
        history was insufficient). Decimal values render via
        ``str(value)`` so the audit-trail bytes stay
        canonical-decimal (REQ_NF_REP_001 family).

        Example::

            metrics = StrategyMetrics(..., rsi_signal=Decimal("68.2"),
                                            atr_signal=Decimal("2.51"))
            metrics.to_signal_reason() == "atr=2.51;rsi=68.2"

        Returns the empty string when every signal field is
        ``None`` — the strategy's emitted ``TradeRationale``
        carries an empty ``signal_reason`` if no indicator was
        consumed (back-compat with strategies that never opted in).
        """
        return format_signal_reason(
            sma_200=self.sma_200_signal,
            rsi=self.rsi_signal,
            atr=self.atr_signal,
            obv=self.obv_signal,
            adx=self.adx_signal,
            vix=self.vix_signal,
        )


_INDICATOR_NAMES = ("adx", "atr", "obv", "rsi", "sma_200", "vix")


def format_signal_reason(
    *,
    sma_200: Decimal | None = None,
    rsi: Decimal | None = None,
    atr: Decimal | None = None,
    obv: Decimal | None = None,
    adx: Decimal | None = None,
    vix: Decimal | None = None,
) -> str:
    """Canonical ``signal_reason`` formatter.

    Standalone helper for callers that don't have a
    ``StrategyMetrics`` on hand (e.g., a strategy that
    consumes the CR-028 indicators directly without first
    building a metrics row).

    Format: ``"name=value;name=value;..."`` sorted by
    indicator name; ``None``-valued indicators are omitted.
    Returns ``""`` when every argument is ``None``.

    Determinism: identical kwargs SHALL produce
    byte-identical strings — a precondition for the
    persistence-layer JSON round-trip + the
    backtest-engine replay invariant.
    """
    readings = {
        "sma_200": sma_200,
        "rsi": rsi,
        "atr": atr,
        "obv": obv,
        "adx": adx,
        "vix": vix,
    }
    pairs = [
        f"{name}={value}"
        for name in _INDICATOR_NAMES
        if (value := readings[name]) is not None
    ]
    return ";".join(pairs)
