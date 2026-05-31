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
from trading_system.models.phase import MarketRegime
from trading_system.models.trading import OrderType, Side, StopLoss


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
    # CR-030 (REQ_F_SRD_002) — order_type the runtime SHALL use
    # when materialising this proposal into an Order. Default
    # ``OrderType.MARKET`` preserves the pre-CR-030 behaviour for
    # every existing strategy; SRD-aware strategies set
    # `OrderType.SRD_LONG` / `OrderType.SRD_SHORT` so the fill
    # routes through the deferred-settlement path.
    order_type: OrderType = OrderType.MARKET

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

    ``hypothesis_ids`` (CR-002 Phase B — REQ_F_QNT_005) — every
    shipped strategy traces back to at least one VALIDATED
    Hypothesis. The tuple holds the hypothesis ids the best
    accepted candidate was generated from. Sorted lexicographically
    so two reports built from the same hypothesis set serialise
    byte-identically (REQ_NF_QNT_002 family). Empty on cold start
    when no hypothesis-driven generator was wired; populated by
    Phase-B generators that consume the ``HypothesisLibrary``.
    """

    cycle_id: str
    best_strategy_id: StrategyId | None
    deltas: dict[str, Decimal]
    risk_assessment: str
    rejected: tuple[StrategyId, ...]
    rejection_reasons: dict[StrategyId, str]
    generated_at: datetime
    notes: str = field(default="")
    hypothesis_ids: tuple[str, ...] = field(default=())

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
        # REQ_NF_QNT_002 family — hypothesis_ids MUST be sorted +
        # de-duplicated so two reports with the same source
        # hypothesis set produce byte-identical serialisations.
        # Check entry-level invariants first so operators see the
        # most-specific error even if the tuple is also unsorted.
        if self.hypothesis_ids:
            for hid in self.hypothesis_ids:
                if not str(hid).strip():
                    raise ValueError(
                        "ImprovementReport.hypothesis_ids entries must be non-empty"
                    )
            seen = set(self.hypothesis_ids)
            if len(seen) != len(self.hypothesis_ids):
                raise ValueError(
                    "ImprovementReport.hypothesis_ids must be unique; "
                    f"got duplicates in {self.hypothesis_ids}"
                )
            if list(self.hypothesis_ids) != sorted(self.hypothesis_ids):
                raise ValueError(
                    "ImprovementReport.hypothesis_ids must be sorted "
                    "lexicographically for replay determinism; "
                    f"got {self.hypothesis_ids}"
                )


@dataclass(frozen=True, slots=True)
class RotationProposal:
    """Output of the Phase-5+ sector rotator (CR-010 / REQ_F_SCT_007).

    Carries full provenance so the audit log can reconstruct any
    rotation: the originating regime, source / destination sector
    weight maps, the decision timestamp, and the policy id that
    produced the proposal.

    REQ refs:
    - REQ_F_SCT_007 — every rotation proposal carries provenance.
    - REQ_SDD_SCT_004 — frozen dataclass; runtime mutation raises
      ``FrozenInstanceError``.
    """

    source_regime: MarketRegime
    source_weights: dict[str, Decimal]  # sector -> current relative weight
    dest_weights: dict[str, Decimal]  # sector -> target  relative weight
    decided_at: datetime
    policy_id: str

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("RotationProposal.policy_id must be non-empty")
