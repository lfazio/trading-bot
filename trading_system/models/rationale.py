"""``TradeRationale`` — structured audit-trail row paired with every
emitted ``Trade`` (CR-015).

The rationale carries the strategy reasoning + per-gate risk verdicts
+ tax-gate math + meta-loop provenance so an operator debugging a
loss has a complete trace beyond the trade table. The dataclass is
frozen + audit-immutable (REQ_F_RAT_003) — once emitted, never
rewritten.

REQ refs: REQ_F_RAT_001..005, REQ_SDD_RAT_001, REQ_SDD_RAT_002.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from trading_system.models.identifiers import StrategyId, TradeId
from trading_system.result import Err, Ok, Result


# ---------------------------------------------------------------------------
# Closed gate-name vocabulary (REQ_SDD_RAT_002)
# ---------------------------------------------------------------------------
GATE_VOCABULARY: frozenset[str] = frozenset(
    {
        "tax_gate",
        "kill_switch",
        "risk_per_trade",
        "stop_loss",
        "class_cap",
        "correlation",
        "regime",
        "cross_account_concentration",
    }
)


@dataclass(frozen=True, slots=True)
class TradeRationale:
    """Audit row for one emitted ``Trade``.

    Construction-time invariants (REQ_SDD_RAT_001):
    - ``trade_id`` and ``strategy_id`` SHALL be non-empty.
    - Other string fields are allowed-empty so strategies / persistence
      paths that haven't opted in still produce a valid row.
    - ``risk_approval`` accepts any read-only mapping; downstream code
      SHALL NOT mutate it (the dataclass itself is frozen).
    """

    trade_id: TradeId
    strategy_id: StrategyId
    strategy_version: str
    signal_reason: str
    risk_approval: Mapping[str, str]
    tax_gate_decision: str
    improvement_report_id: str
    decided_at: datetime

    def __post_init__(self) -> None:
        if not self.trade_id:
            raise ValueError("TradeRationale.trade_id must be non-empty")
        if not self.strategy_id:
            raise ValueError("TradeRationale.strategy_id must be non-empty")

    def __hash__(self) -> int:  # type: ignore[override]
        # ``Mapping`` is unhashable; we hash a frozenset of items so
        # two rationales built from semantically-identical inputs (in
        # any concrete Mapping type — dict, MappingProxyType, …)
        # produce the same hash. ``frozen=True`` already gives us
        # equality + hashable, but the auto-derived hash chokes on
        # the Mapping field — we override here to keep hashability.
        return hash(
            (
                self.trade_id,
                self.strategy_id,
                self.strategy_version,
                self.signal_reason,
                tuple(sorted(self.risk_approval.items())),
                self.tax_gate_decision,
                self.improvement_report_id,
                self.decided_at,
            )
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TradeRationale):
            return NotImplemented
        return (
            self.trade_id == other.trade_id
            and self.strategy_id == other.strategy_id
            and self.strategy_version == other.strategy_version
            and self.signal_reason == other.signal_reason
            and dict(self.risk_approval) == dict(other.risk_approval)
            and self.tax_gate_decision == other.tax_gate_decision
            and self.improvement_report_id == other.improvement_report_id
            and self.decided_at == other.decided_at
        )


def validate_gate_vocabulary(rationale: TradeRationale) -> Result[None, str]:
    """Audit helper — checks every ``risk_approval`` key is in the
    documented :data:`GATE_VOCABULARY` (REQ_SDD_RAT_002). Returns
    ``Err("rationale:unknown_gate:<name>")`` for the first unknown
    key. Operators run this in CI / spot-checks; the dataclass
    itself stays permissive so a future SDD amendment can grow the
    vocabulary without breaking existing rationales."""
    for gate in rationale.risk_approval:
        if gate not in GATE_VOCABULARY:
            return Err(f"rationale:unknown_gate:{gate}")
    return Ok(None)
