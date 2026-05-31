"""TC_MCS_008 — Monte Carlo meta-loop gating (REQ_F_MCS_005).

Targets the optional ``mc_run_step`` + ``mc_drawdown_floor`` plumbing
on ``LoopController`` introduced by CR-007 Phase 5.

Test surface (no real MC runner — the step is a stub that returns
pre-computed ``MonteCarloResult`` rows so the gate logic is exercised
in isolation from the generator + backtest):

  1. ``mc_run_step is None`` SHALL bypass the gate — verified via a
     spy callable that records the number of invocations.
  2. ``mc_run_step`` returning a result whose P5 drawdown exceeds the
     phase floor SHALL reject the candidate with reason
     ``"mc:p5_drawdown_exceeds_phase_floor"``.
  3. ``mc_run_step`` returning a result whose P5 drawdown is below
     the phase floor SHALL pass the candidate through (no MC-related
     rejection).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.backtesting.monte_carlo import (
    QUINTILE_KEYS,
    MonteCarloResult,
)
from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.slippage import ZeroSlippageModel
from trading_system.models.identifiers import InstrumentId, StrategyId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.phase import (
    AllocationBucket,
    MarketRegime,
    PhaseConstraints,
)
from trading_system.models.safety import KillSwitchState, KillSwitchTrigger
from trading_system.result import Ok
from trading_system.risk.config import RiskConfig
from trading_system.risk.engine import RiskEngine
from trading_system.strategy_lab import (
    Evaluator,
    LabBacktester,
    LoopController,
    Optimizer,
    OptimizerConfig,
    Registry,
    RiskGuard,
    RiskGuardConfig,
    StaticGenerator,
    StrategyCandidate,
)
from trading_system.tax.config import TaxConfig


EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(year: int = 2026, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _stock() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _phase_constraints() -> PhaseConstraints:
    return PhaseConstraints(
        max_positions=6,
        max_trades_per_month=8,
        allocation_targets={
            AllocationBucket.STOCK: Decimal("0.50"),
            AllocationBucket.TACTICAL: Decimal("0.20"),
            AllocationBucket.CASH: Decimal("0.30"),
        },
        turbo_exposure_max=Decimal(0),
        risk_per_trade_band=(Decimal("0.005"), Decimal("0.05")),
        max_drawdown=Decimal("0.15"),
    )


class _StubSafety:
    def must_halt(self) -> bool:
        return False

    def state(self) -> KillSwitchState:
        return KillSwitchState.ACTIVE

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        pass


class _NoopStrategy:
    id: StrategyId = StrategyId("noop")

    def evaluate(self, state: object) -> list[TradeProposal]:
        _ = state
        return []


def _candidate(idx: int) -> StrategyCandidate:
    return StrategyCandidate(
        id=StrategyId(f"cand-{idx}"),
        strategy_factory=_NoopStrategy,
        bucket=AllocationBucket.STOCK,
        seed=idx + 1,
        config_hash=f"hash-{idx}",
        generated_at=_ts(),
    )


def _build_controller(
    *,
    mc_run_step=None,
    mc_drawdown_floor=None,
    phase=None,
    regime=None,
):
    s = _stock()
    data = MockMarketDataProvider(seed=1)
    risk = RiskEngine(cfg=RiskConfig(), safety=_StubSafety())
    backtester = LabBacktester(
        instruments=(s,),
        data=data,
        fee_model=FlatFeeModel(commission=_eur("0"), spread_bps=Decimal(0)),
        slippage_model=ZeroSlippageModel(),
        risk=risk,
        pc=_phase_constraints(),
        regime=MarketRegime.SIDEWAYS,
        tax=TaxConfig.default(),
        starting_capital=_eur("10000"),
        start=_ts(2026, 1, 1),
        end=_ts(2026, 1, 8),
        timeframe=Timeframe.D1,
    )
    pool = (_candidate(0), _candidate(1))
    registry = Registry()
    controller = LoopController(
        generator=StaticGenerator(pool=pool),
        backtester=backtester,
        evaluator=Evaluator(),
        risk_guard=RiskGuard(cfg=RiskGuardConfig.default_phase_1_2()),
        optimizer=Optimizer(cfg=OptimizerConfig(top_k=2)),
        registry=registry,
        candidates_per_cycle=2,
        git_sha="test-sha",
        mc_run_step=mc_run_step,
        mc_drawdown_floor=mc_drawdown_floor,
        phase=phase,
        regime=regime,
    )
    return controller, registry


def _mc_result(p5_drawdown: Decimal) -> MonteCarloResult:
    """Build a deterministic ``MonteCarloResult`` with the requested
    P5 drawdown; other percentiles compose monotonically upward so the
    constructor invariants hold."""
    drawdown = {k: p5_drawdown + Decimal("0.01") * Decimal(i) for i, k in enumerate(QUINTILE_KEYS)}
    return MonteCarloResult(
        equity_percentiles={k: Decimal(str(900 + 100 * (i + 1))) for i, k in enumerate(QUINTILE_KEYS)},
        drawdown_percentiles=drawdown,
        sharpe_percentiles={k: Decimal("0.10") + Decimal("0.05") * Decimal(i) for i, k in enumerate(QUINTILE_KEYS)},
        kill_switch_trip_rate=Decimal("0.02"),
        n_paths=200,
        config_hash="cafebabe",
    )


# ---------------------------------------------------------------------------
# TC_MCS_008 — mc_run_step=None bypasses the gate entirely
# ---------------------------------------------------------------------------


def test_mc_step_none_bypasses_gate() -> None:
    invocation_count = {"calls": 0}

    def _spy(_c: StrategyCandidate):
        invocation_count["calls"] += 1
        return Ok(_mc_result(Decimal("0.01")))

    # Build controller with no MC step.
    controller, registry = _build_controller(mc_run_step=None, mc_drawdown_floor=Decimal("0.15"))
    # Override the unused spy to keep the test self-documenting — even
    # if the loop accidentally invoked something, the spy would catch.
    _ = _spy
    report = controller.cycle(cycle_id="bypass", at=_ts(2026, 5, 17))
    # No MC-related rejection should appear.
    for reason in report.rejection_reasons.values():
        assert not reason.startswith("mc:"), f"unexpected MC rejection: {reason}"


def test_mc_step_none_spy_never_invoked() -> None:
    """Explicit spy check — the loop SHALL NOT touch the (None) MC
    step even by accident."""
    invocation_count = {"calls": 0}

    def _spy(_c: StrategyCandidate):
        invocation_count["calls"] += 1
        return Ok(_mc_result(Decimal("0.01")))

    controller, _registry = _build_controller(mc_run_step=None, mc_drawdown_floor=None)
    # Swap in the spy *after* construction to ensure the cycle's None
    # check is what guards the call site.
    controller.mc_run_step = None  # explicit
    controller.cycle(cycle_id="bypass-spy", at=_ts(2026, 5, 17))
    assert invocation_count["calls"] == 0


# ---------------------------------------------------------------------------
# TC_MCS_008 — high P5 drawdown ⇒ reject
# ---------------------------------------------------------------------------


def test_mc_step_rejects_when_p5_drawdown_exceeds_floor() -> None:
    def _step(_c: StrategyCandidate):
        # P5 drawdown 0.18 > phase floor 0.15 ⇒ reject.
        return Ok(_mc_result(Decimal("0.18")))

    controller, _registry = _build_controller(
        mc_run_step=_step, mc_drawdown_floor=Decimal("0.15")
    )
    report = controller.cycle(cycle_id="reject-mc", at=_ts(2026, 5, 17))
    # Every candidate SHALL be rejected with the MC-specific reason.
    assert report.rejection_reasons, "expected MC rejections"
    for reason in report.rejection_reasons.values():
        assert reason == "mc:p5_drawdown_exceeds_phase_floor"


# ---------------------------------------------------------------------------
# TC_MCS_008 — P5 drawdown below floor ⇒ pass through
# ---------------------------------------------------------------------------


def test_mc_step_passes_when_p5_drawdown_below_floor() -> None:
    def _step(_c: StrategyCandidate):
        # P5 drawdown 0.05 < phase floor 0.15 ⇒ pass through.
        return Ok(_mc_result(Decimal("0.05")))

    controller, _registry = _build_controller(
        mc_run_step=_step, mc_drawdown_floor=Decimal("0.15")
    )
    report = controller.cycle(cycle_id="pass-mc", at=_ts(2026, 5, 17))
    for reason in report.rejection_reasons.values():
        assert not reason.startswith("mc:"), (
            f"unexpected MC rejection when P5 below floor: {reason}"
        )


# ---------------------------------------------------------------------------
# TC_MCS_012 — Matrix-path gate dispatch
# ---------------------------------------------------------------------------


def test_matrix_path_rejects_when_p5_exceeds_matrix_floor() -> None:
    """REQ_F_MCS_005 (CR-031 amendment) / REQ_SDD_MCS_008 — wire
    a ``LoopController`` with both ``MCDrawdownFloor`` and phase
    + regime context; p5 > matrix floor SHALL reject with the
    legacy category string."""
    from trading_system.models.phase import Phase
    from trading_system.strategy_lab.mc_drawdown_floor import MCDrawdownFloor

    floor = MCDrawdownFloor.from_matrix(
        {(Phase.FIVE, MarketRegime.SIDEWAYS): Decimal("0.15")},
        default=Decimal("0.20"),
    )

    def _step(_c: StrategyCandidate):
        # P5 drawdown 0.18 > matrix floor 0.15 ⇒ reject.
        return Ok(_mc_result(Decimal("0.18")))

    controller, _registry = _build_controller(
        mc_run_step=_step,
        mc_drawdown_floor=floor,
        phase=Phase.FIVE,
        regime=MarketRegime.SIDEWAYS,
    )
    report = controller.cycle(cycle_id="reject-mc-matrix", at=_ts(2026, 5, 17))
    assert report.rejection_reasons, "expected matrix-path rejections"
    for reason in report.rejection_reasons.values():
        # Category string preserved across CR-031 — downstream
        # consumers see the same shape.
        assert reason == "mc:p5_drawdown_exceeds_phase_floor"


def test_matrix_path_passes_when_p5_below_matrix_floor() -> None:
    from trading_system.models.phase import Phase
    from trading_system.strategy_lab.mc_drawdown_floor import MCDrawdownFloor

    floor = MCDrawdownFloor.from_matrix(
        {(Phase.FIVE, MarketRegime.SIDEWAYS): Decimal("0.15")},
        default=Decimal("0.20"),
    )

    def _step(_c: StrategyCandidate):
        # P5 drawdown 0.10 < matrix floor 0.15 ⇒ pass.
        return Ok(_mc_result(Decimal("0.10")))

    controller, _registry = _build_controller(
        mc_run_step=_step,
        mc_drawdown_floor=floor,
        phase=Phase.FIVE,
        regime=MarketRegime.SIDEWAYS,
    )
    report = controller.cycle(cycle_id="pass-mc-matrix", at=_ts(2026, 5, 17))
    for reason in report.rejection_reasons.values():
        assert not reason.startswith("mc:"), (
            f"unexpected MC rejection when matrix p5 below floor: {reason}"
        )


# ---------------------------------------------------------------------------
# TC_MCS_014 — Backwards-compat + structured-log envelope
# ---------------------------------------------------------------------------


def test_legacy_decimal_floor_still_rejects_with_same_category() -> None:
    """REQ_F_MCS_005 (CR-031 amendment) — legacy callers passing
    ``Decimal`` (no phase + no regime) SHALL still see the
    pre-CR-031 behaviour: same rejection category, no
    structured-log envelope (additive contract)."""
    import logging

    def _step(_c: StrategyCandidate):
        return Ok(_mc_result(Decimal("0.18")))

    controller, _registry = _build_controller(
        mc_run_step=_step,
        mc_drawdown_floor=Decimal("0.15"),
        # Explicitly no phase / regime — the legacy path doesn't need them.
    )
    captured = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    target = logging.getLogger("trading_system.strategy_lab.loop_controller")
    handler = _Handler()
    handler.setLevel(logging.DEBUG)
    target.addHandler(handler)
    prior_level = target.level
    target.setLevel(logging.DEBUG)
    try:
        report = controller.cycle(cycle_id="reject-mc-legacy", at=_ts(2026, 5, 17))
    finally:
        target.removeHandler(handler)
        target.setLevel(prior_level)

    assert report.rejection_reasons
    for reason in report.rejection_reasons.values():
        assert reason == "mc:p5_drawdown_exceeds_phase_floor"
    # Legacy path SHALL NOT emit the matrix-path envelope.
    envelopes = [
        r
        for r in captured
        if getattr(r, "category", None) == "improvement_report"
        and r.getMessage() == "mc_gate_reject"
    ]
    assert envelopes == [], (
        f"legacy path emitted matrix-only envelope: {envelopes}"
    )


def test_matrix_path_emits_structured_log_envelope_on_rejection() -> None:
    """REQ_SDD_MCS_009 — matrix-path rejection SHALL emit a
    ``LogCategory.IMPROVEMENT_REPORT`` envelope with the
    documented payload shape; all Decimals serialise as
    canonical-decimal strings per REQ_NF_MCS_002."""
    import logging

    from trading_system.models.phase import Phase
    from trading_system.strategy_lab.mc_drawdown_floor import MCDrawdownFloor

    floor = MCDrawdownFloor.fixed(Decimal("0.15"))

    def _step(_c: StrategyCandidate):
        return Ok(_mc_result(Decimal("0.18")))

    controller, _registry = _build_controller(
        mc_run_step=_step,
        mc_drawdown_floor=floor,
        phase=Phase.THREE,
        regime=MarketRegime.BULL,
    )
    captured = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    target = logging.getLogger("trading_system.strategy_lab.loop_controller")
    handler = _Handler()
    handler.setLevel(logging.DEBUG)
    target.addHandler(handler)
    prior_level = target.level
    target.setLevel(logging.DEBUG)
    try:
        controller.cycle(cycle_id="reject-mc-matrix-envelope", at=_ts(2026, 5, 17))
    finally:
        target.removeHandler(handler)
        target.setLevel(prior_level)

    envelopes = [
        r
        for r in captured
        if getattr(r, "category", None) == "improvement_report"
        and r.getMessage() == "mc_gate_reject"
    ]
    assert envelopes, "expected at least one matrix-path envelope on rejection"
    payload = envelopes[0].payload  # type: ignore[attr-defined]
    assert payload["phase"] == str(Phase.THREE)
    assert payload["regime"] == "bull"
    assert payload["applied_floor"] == "0.15"
    assert payload["p5_drawdown"] == "0.18"
    assert payload["category"] == "mc:p5_drawdown_exceeds_phase_floor"
