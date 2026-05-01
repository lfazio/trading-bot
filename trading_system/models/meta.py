"""Trade proposals, validation results, and meta-loop reports.

REQ refs:
- REQ_F_MTO_007 — ``ImprovementReport`` shape (best id, deltas, risk
  assessment, rejected ids, reasons, generated_at).
- REQ_SDD_LOG_003 — log-record fields for ``ImprovementReport``.
- REQ_F_TAX_003 — ``TradeProposal`` carries ``expected_net_profit``
  and ``expected_fees`` so the tax-aware gate can run pre-execution.
- REQ_SDD_DAT_005 — ``expected_fees`` is the *estimate*; the executed
  fee lives on ``Trade.fees`` only.
- REQ_SDD_TYP_001 — ``Decimal`` for percentages and money fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_system.models.identifiers import StrategyId
from trading_system.models.instrument import Instrument
from trading_system.models.money import Money
from trading_system.models.trading import Side, StopLoss


@dataclass(frozen=True, slots=True)
class TradeProposal:
    """Strategy's intent to trade — pre-tax-gate, pre-risk-gate,
    pre-execution. Fees and profit are *estimates* used for the gate
    decision (REQ_F_TAX_003)."""

    instrument: Instrument
    side: Side
    size_pct_of_capital: Decimal  # 0..1
    expected_net_profit: Money
    expected_fees: Money
    stop_loss: StopLoss
    source_strategy: StrategyId

    def __post_init__(self) -> None:
        if not (0 < self.size_pct_of_capital <= 1):
            raise ValueError(
                f"TradeProposal.size_pct_of_capital must be in (0, 1], "
                f"got {self.size_pct_of_capital}"
            )
        if self.expected_fees.amount < 0:
            raise ValueError(
                f"TradeProposal.expected_fees must be >= 0, got {self.expected_fees.amount}"
            )
        if self.expected_fees.currency != self.expected_net_profit.currency:
            raise ValueError(
                "TradeProposal.expected_fees and expected_net_profit must share a currency"
            )


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of a pre-trade or post-trade gate. Distinct from
    ``Result[T, E]`` because gate evaluation MAY accumulate multiple
    rejection reasons in a single pass (e.g., size-out-of-band AND
    correlation-breach)."""

    passed: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.passed and self.reasons:
            raise ValueError(
                f"ValidationResult.passed=True must carry no reasons; got {self.reasons!r}"
            )
        if not self.passed and not self.reasons:
            raise ValueError("ValidationResult.passed=False must carry at least one reason")

    @classmethod
    def accept(cls) -> ValidationResult:
        return cls(passed=True, reasons=())

    @classmethod
    def reject(cls, *reasons: str) -> ValidationResult:
        if not reasons:
            raise ValueError("ValidationResult.reject requires at least one reason")
        return cls(passed=False, reasons=tuple(reasons))


@dataclass(frozen=True, slots=True)
class ImprovementReport:
    """Per-cycle output of the meta-optimization loop (REQ_F_MTO_007).

    ``deltas`` carries metric-name → delta (e.g., {"return": Decimal("0.012"),
    "drawdown": Decimal("-0.005"), "sharpe": Decimal("0.18")}).
    """

    cycle_id: str
    best_strategy_id: StrategyId | None
    deltas: dict[str, Decimal]
    risk_assessment: str
    rejected: tuple[StrategyId, ...]
    rejection_reasons: dict[StrategyId, str]
    generated_at: datetime
    notes: str = field(default="")

    def __post_init__(self) -> None:
        if not self.cycle_id:
            raise ValueError("ImprovementReport.cycle_id must be non-empty")
        # Every rejected id must have a recorded reason.
        rejected_set = set(self.rejected)
        reason_keys = set(self.rejection_reasons.keys())
        if rejected_set != reason_keys:
            missing = rejected_set - reason_keys
            extra = reason_keys - rejected_set
            raise ValueError(
                "ImprovementReport.rejected and rejection_reasons keys must match; "
                f"missing reasons for {sorted(missing)}, extra reasons for {sorted(extra)}"
            )
        if self.best_strategy_id is None and not self.rejected:
            raise ValueError(
                "ImprovementReport must record either an accepted best_strategy_id "
                "or at least one rejection"
            )
