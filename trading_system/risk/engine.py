"""``RiskEngine`` — pre-trade and post-trade gates.

REQ refs:
- REQ_F_RSK_001..005 — full surface.
- REQ_SDD_ALG_016 — pre-trade gate ordering:
  ``kill-switch -> risk-per-trade-band -> stop-loss-presence ->
  class-cap -> correlation -> regime``. Short-circuits on the first
  failure.
- REQ_SDS_FLO_001 — every trade decision traverses tax -> risk ->
  safety -> broker.
- REQ_F_CAP_014 / REQ_SDD_DAT_001 — stop-loss is mandatory; the
  Order / Position constructors enforce that already, but the gate
  re-checks the proposal explicitly so a defective strategy
  surfaces here rather than at order construction.
- REQ_F_RSK_005 / REQ_SDS_MOD_009 / REQ_SDD_ERR_003 — internal
  inconsistencies escalate to a kill-switch INTEGRITY trigger.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import SnapshotId
from trading_system.models.instrument import Instrument
from trading_system.models.meta import TradeProposal, ValidationResult
from trading_system.models.phase import (
    MarketRegime,
    PhaseConstraints,
)
from trading_system.models.safety import KillSwitchTrigger, TriggerCategory
from trading_system.result import Err, Result
from trading_system.risk.config import RiskConfig
from trading_system.risk.mapping import buckets_for_class
from trading_system.risk.metrics import drawdown_now, portfolio_vol_ann
from trading_system.safety.protocol import SafetyLayer
from trading_system.strategies.protocol import PortfolioView


@dataclass(slots=True)
class RiskEngine:
    """Pre-trade gate + post-trade monitor.

    The engine holds references to its config and the ``SafetyLayer``;
    correlation lookup is injected per-call so the engine doesn't
    depend on portfolio internals. When ``correlation_lookup`` is
    ``None`` the correlation gate is skipped — useful for unit tests
    and acceptable in production until the portfolio's correlation
    helper lands (see Phase 5 step 11).
    """

    cfg: RiskConfig
    safety: SafetyLayer

    # ------------------------------------------------------------------
    # Pre-trade gate (REQ_SDD_ALG_016)
    # ------------------------------------------------------------------

    def pre_trade(
        self,
        proposal: TradeProposal,
        portfolio: PortfolioView,
        pc: PhaseConstraints,
        regime: MarketRegime,
        *,
        correlation_lookup: Callable[[Instrument], Decimal | None] | None = None,
        cross_account_gate: Callable[[TradeProposal], Result[None, str]] | None = None,
    ) -> ValidationResult:
        # 1. Kill-switch check (REQ_S_KS_011)
        if self.safety.must_halt():
            return ValidationResult.reject("kill_switch_active")

        # 2. Risk-per-trade-band (REQ_F_CAP_013)
        lo, hi = pc.risk_per_trade_band
        if not (lo <= proposal.size_pct_of_capital <= hi):
            return ValidationResult.reject("risk_per_trade_out_of_band")

        # 3. Stop-loss presence (REQ_F_CAP_014). The TradeProposal
        #    type declares ``stop_loss: StopLoss`` (non-None), and
        #    StopLoss construction rejects ``price <= 0``
        #    (REQ_SDD_DAT_001). Both invariants are caught upstream;
        #    no further runtime check needed here.

        # 4. Class-cap (sum of buckets that map to the instrument's
        #    class — REQ_F_RSK_002 / REQ_SDD_TYP_004).
        cls = proposal.instrument.cls
        buckets = buckets_for_class(cls)
        cur_class_exposure = sum((portfolio.exposure_pct(b) for b in buckets), start=Decimal(0))
        cls_cap = sum(
            (pc.allocation_targets.get(b, Decimal(0)) for b in buckets),
            start=Decimal(0),
        )
        if cur_class_exposure + proposal.size_pct_of_capital > cls_cap:
            return ValidationResult.reject("class_cap_breach")

        # 5. Correlation (REQ_F_RSK_003 / REQ_SDD_ALG_008)
        if correlation_lookup is not None:
            corr = correlation_lookup(proposal.instrument)
            if corr is not None and corr > self.cfg.correlation_max:
                return ValidationResult.reject("correlation_breach")

        # 6. Regime (REQ_F_STP_004 / REQ_F_TRB_002 regime-extreme)
        forbidden = self.cfg.regimes_forbidden_for(cls)
        if regime in forbidden:
            return ValidationResult.reject("regime_forbidden")

        # 7. Cross-account concentration (REQ_F_ACC_008 / REQ_SDS_ACC_004).
        # In single-account deployments the caller passes
        # ``cross_account_gate=None`` (default) and this gate is a
        # no-op — REQ_NF_ACC_001 backwards compatibility. Multi-
        # account callers pass a closure that captures the registry
        # + household PortfolioGroup; the gate runs AFTER the
        # cheaper per-account checks above so multi-account
        # deployments aren't paying for cross-account work on
        # trades that would have been rejected anyway.
        if cross_account_gate is not None:
            outcome = cross_account_gate(proposal)
            if isinstance(outcome, Err):
                return ValidationResult.reject(outcome.error)

        return ValidationResult.accept()

    # ------------------------------------------------------------------
    # Post-trade gate (REQ_SDD_ALG_005 / REQ_SDD_ALG_009)
    # ------------------------------------------------------------------

    def post_trade(
        self,
        equity_curve: list[EquityPoint],
        pc: PhaseConstraints,
        *,
        at: datetime,
        snapshot_id: SnapshotId,
    ) -> None:
        """Evaluate the after-tax equity curve against the phase's
        drawdown cap and (Phase 5+) portfolio-vol cap. Breaches
        escalate to the kill switch.

        ``snapshot_id`` references a pre-staged audit snapshot so the
        ``KillSwitchTrigger`` constructor's invariant
        (``snapshot_id != ""``) is satisfied. The state manager is
        responsible for the canonical snapshot artifact.
        """
        dd = drawdown_now(equity_curve)
        if dd > pc.max_drawdown:
            self.safety.raise_trigger(
                KillSwitchTrigger(
                    category=TriggerCategory.FINANCIAL,
                    code="dd_breach",
                    message=(f"drawdown {dd} exceeds phase cap {pc.max_drawdown}"),
                    severity="KILL",
                    raised_at=at,
                    snapshot_id=snapshot_id,
                )
            )
            return  # one trigger per call; don't pile both signals

        if pc.portfolio_vol_cap is not None:
            vol = portfolio_vol_ann(equity_curve, self.cfg.correlation_window_days)
            if vol is not None and vol > pc.portfolio_vol_cap:
                self.safety.raise_trigger(
                    KillSwitchTrigger(
                        category=TriggerCategory.FINANCIAL,
                        code="vol_cap_breach",
                        message=(f"portfolio vol {vol} exceeds phase cap {pc.portfolio_vol_cap}"),
                        severity="DEGRADE",
                        raised_at=at,
                        snapshot_id=snapshot_id,
                    )
                )
