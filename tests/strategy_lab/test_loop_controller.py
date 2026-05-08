"""Integration test for the meta-loop pipeline.

Runs ``LoopController.cycle()`` end-to-end on a tiny synthetic
universe with a no-op strategy. The point is to exercise every
pipeline step (generate -> backtest -> evaluate -> guard -> score
-> select -> registry -> report) in the same orchestration the
operator would use.

REQ refs: REQ_F_MTO_002 (8-step pipeline), REQ_F_MTO_005 (registry
storage), REQ_F_MTO_006 (safe-self-improvement filter),
REQ_F_MTO_007 (ImprovementReport shape), REQ_NF_DET_001.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

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

    def evaluate(self, state) -> list[TradeProposal]:
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


def _build_controller(*, candidates_per_cycle: int = 2) -> tuple[LoopController, Registry]:
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
    pool = tuple(_candidate(i) for i in range(candidates_per_cycle))
    registry = Registry()
    controller = LoopController(
        generator=StaticGenerator(pool=pool),
        backtester=backtester,
        evaluator=Evaluator(),
        risk_guard=RiskGuard(cfg=RiskGuardConfig.default_phase_1_2()),
        optimizer=Optimizer(cfg=OptimizerConfig(top_k=2)),
        registry=registry,
        candidates_per_cycle=candidates_per_cycle,
        git_sha="test-sha",
    )
    return controller, registry


# ---------------------------------------------------------------------------
# End-to-end happy path
# ---------------------------------------------------------------------------


class TestCycle:
    def test_cold_start_accepts_top_k_and_emits_report(self) -> None:
        controller, registry = _build_controller(candidates_per_cycle=2)
        report = controller.cycle(cycle_id="cycle-001", at=_ts(2026, 5, 8))
        # Report shape (REQ_F_MTO_007).
        assert report.cycle_id == "cycle-001"
        assert report.best_strategy_id is not None
        # Both no-op candidates accepted on cold start.
        assert len(registry.list_experimental()) == 2
        assert registry.list_validated() == ()
        # Rejection map keys equal rejection_reasons keys (invariant
        # enforced on ImprovementReport).
        assert set(report.rejected) == set(report.rejection_reasons.keys())

    def test_subsequent_cycle_with_baseline(self) -> None:
        controller, registry = _build_controller(candidates_per_cycle=2)
        controller.cycle(cycle_id="cycle-001", at=_ts(2026, 5, 8))
        # Promote one entry to validated and pin baseline.
        registry.mark_validated(StrategyId("cand-0"))
        registry.set_baseline(StrategyId("cand-0"))

        # Drive the controller again with a fresh pool.
        pool = (_candidate(2), _candidate(3))
        controller.generator = StaticGenerator(pool=pool)
        report = controller.cycle(cycle_id="cycle-002", at=_ts(2026, 5, 9))
        # No-op candidates have identical metrics to the baseline ->
        # equal-ratio rejected by the optimizer (strict > comparator,
        # REQ_F_MTO_006).
        assert report.best_strategy_id is None
        # Both new candidates rejected with optimizer reasons.
        assert "optimizer:" in report.rejection_reasons[StrategyId("cand-2")]
        assert "optimizer:" in report.rejection_reasons[StrategyId("cand-3")]


# ---------------------------------------------------------------------------
# Determinism: identical inputs -> identical report
# ---------------------------------------------------------------------------


def test_cycle_is_deterministic() -> None:
    c1, _ = _build_controller(candidates_per_cycle=2)
    c2, _ = _build_controller(candidates_per_cycle=2)
    r1 = c1.cycle(cycle_id="cycle", at=_ts(2026, 5, 8))
    r2 = c2.cycle(cycle_id="cycle", at=_ts(2026, 5, 8))
    assert r1.best_strategy_id == r2.best_strategy_id
    assert r1.deltas == r2.deltas
    assert r1.rejection_reasons == r2.rejection_reasons
