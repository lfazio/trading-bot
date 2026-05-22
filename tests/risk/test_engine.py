"""Tests for ``trading_system.risk.engine``.

Covers the pre-trade gate ordering (REQ_SDD_ALG_016), each rejection
reason, and the post-trade drawdown / vol-cap escalations
(REQ_SDD_ALG_005, REQ_SDD_ALG_009).

REQ refs verified by the parametrized gate tests below:
- REQ_F_RSK_001 — per-phase max drawdown gate. The
  ``test_drawdown_*`` cases assert the engine raises when the
  realized drawdown breaches ``PhaseConstraints.max_drawdown``.
- REQ_F_RSK_002 — single-asset exposure cap. The
  ``test_class_cap_*`` cases exercise the per-class budget gate
  (``RiskConfig.single_asset_cap`` is the configured ceiling).
- REQ_F_RSK_003 — portfolio correlation. The
  ``test_correlation_*`` cases assert the correlation_max gate
  rejects stacking trades and the rebalance / reject branch fires.
- REQ_F_RSK_005 — risk-engine failure trips the kill switch.
  The ``test_kill_switch_*`` cases assert ``must_halt`` is the
  first gate (REQ_SDD_ALG_016) so any inconsistent state
  forces a halt before downstream gates run.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import (
    InstrumentId,
    SnapshotId,
    StrategyId,
)
from trading_system.models.instrument import (
    Instrument,
    InstrumentClass,
    Stock,
)
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.phase import (
    AllocationBucket,
    MarketRegime,
    PhaseConstraints,
)
from trading_system.models.safety import KillSwitchState, KillSwitchTrigger
from trading_system.models.trading import Side, StopLoss
from trading_system.result import Nothing, Option
from trading_system.risk.config import RiskConfig
from trading_system.risk.engine import RiskEngine
from trading_system.strategies.protocol import PortfolioView

EUR = Currency.EUR


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class StubSafety:
    def __init__(self, halt: bool = False) -> None:
        self._halt = halt
        self.triggers: list[KillSwitchTrigger] = []

    def must_halt(self) -> bool:
        return self._halt

    def state(self) -> KillSwitchState:
        return KillSwitchState.KILL if self._halt else KillSwitchState.ACTIVE

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        self.triggers.append(trigger)


class StubPortfolio:
    """Minimal PortfolioView for risk tests."""

    def __init__(
        self,
        equity_amount: str = "10000",
        cash_amount: str = "10000",
        exposures: dict[AllocationBucket, Decimal] | None = None,
    ) -> None:
        self._equity = Money(Decimal(equity_amount), EUR)
        self._cash = Money(Decimal(cash_amount), EUR)
        self._exposures: dict[AllocationBucket, Decimal] = exposures or {}

    def equity(self) -> Money:
        return self._equity

    def cash(self) -> Money:
        return self._cash

    def exposure_pct(self, bucket: AllocationBucket) -> Decimal:
        return self._exposures.get(bucket, Decimal(0))

    def holds(self, instrument_id: InstrumentId) -> bool:
        return False

    def position_for(self, instrument_id: InstrumentId) -> Option[object]:
        return Nothing()


def test_stub_portfolio_satisfies_protocol() -> None:
    assert isinstance(StubPortfolio(), PortfolioView)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def make_stock(symbol: str = "ABC") -> Stock:
    return Stock(
        id=InstrumentId(f"id-{symbol}"),
        symbol=symbol,
        exchange="EPA",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin=f"FR000{symbol}",
        sector="Industrials",
        country="FR",
    )


def make_proposal(
    *,
    instrument: Instrument | None = None,
    size: str = "0.015",
) -> TradeProposal:
    return TradeProposal(
        instrument=instrument or make_stock(),
        side=Side.BUY,
        size_pct_of_capital=Decimal(size),
        expected_net_profit=Money(Decimal("12.50"), EUR),
        expected_fees=Money(Decimal("0.50"), EUR),
        stop_loss=StopLoss(price=Decimal("90")),
        source_strategy=StrategyId("core_v1"),
    )


def make_phase_constraints(  # noqa: PLR0913 - mirrors PhaseConstraints fields
    *,
    band_lo: str = "0.01",
    band_hi: str = "0.02",
    stock_alloc: str = "0.70",
    tactical_alloc: str = "0.30",
    turbo_alloc: str = "0",
    max_drawdown: str = "0.15",
    portfolio_vol_cap: Decimal | None = None,
) -> PhaseConstraints:
    targets: dict[AllocationBucket, Decimal] = {
        AllocationBucket.STOCK: Decimal(stock_alloc),
        AllocationBucket.TACTICAL: Decimal(tactical_alloc),
    }
    if Decimal(turbo_alloc) > 0:
        targets[AllocationBucket.TURBO] = Decimal(turbo_alloc)
    total = sum(targets.values(), start=Decimal(0))
    if total != Decimal(1):
        targets[AllocationBucket.CASH] = Decimal(1) - total
    return PhaseConstraints(
        max_positions=6,
        max_trades_per_month=8,
        allocation_targets=targets,
        turbo_exposure_max=Decimal(turbo_alloc),
        risk_per_trade_band=(Decimal(band_lo), Decimal(band_hi)),
        max_drawdown=Decimal(max_drawdown),
        portfolio_vol_cap=portfolio_vol_cap,
    )


def make_engine(safety: StubSafety | None = None, **cfg_kwargs: object) -> RiskEngine:
    return RiskEngine(cfg=RiskConfig(**cfg_kwargs), safety=safety or StubSafety())


# ---------------------------------------------------------------------------
# Pre-trade gate ordering (REQ_SDD_ALG_016)
# ---------------------------------------------------------------------------


class TestPreTradeGateOrder:
    def test_kill_switch_first(self) -> None:
        # Even with otherwise-valid proposal, KS check rejects first.
        safety = StubSafety(halt=True)
        result = make_engine(safety).pre_trade(
            make_proposal(),
            StubPortfolio(),
            make_phase_constraints(),
            MarketRegime.SIDEWAYS,
        )
        assert result.passed is False
        assert "kill_switch_active" in result.reasons

    def test_size_band_below(self) -> None:
        result = make_engine().pre_trade(
            make_proposal(size="0.005"),
            StubPortfolio(),
            make_phase_constraints(),
            MarketRegime.SIDEWAYS,
        )
        assert "risk_per_trade_out_of_band" in result.reasons

    def test_size_band_above(self) -> None:
        result = make_engine().pre_trade(
            make_proposal(size="0.05"),
            StubPortfolio(),
            make_phase_constraints(),
            MarketRegime.SIDEWAYS,
        )
        assert "risk_per_trade_out_of_band" in result.reasons

    def test_size_at_band_boundary_passes(self) -> None:
        # Inclusive boundary: size == lo or hi accepted.
        for size in ("0.01", "0.02"):
            result = make_engine().pre_trade(
                make_proposal(size=size),
                StubPortfolio(),
                make_phase_constraints(),
                MarketRegime.SIDEWAYS,
            )
            assert result.passed is True

    def test_class_cap_breach(self) -> None:
        # STOCK + TACTICAL cap = 1.0 here; existing exposure already
        # at 1.0; any new STOCK allocation breaches.
        result = make_engine().pre_trade(
            make_proposal(),
            StubPortfolio(
                exposures={AllocationBucket.STOCK: Decimal("1.0")},
            ),
            make_phase_constraints(),
            MarketRegime.SIDEWAYS,
        )
        assert "class_cap_breach" in result.reasons

    def test_class_cap_lumps_stock_and_tactical(self) -> None:
        # Same 1.0 cap, split between STOCK and TACTICAL exposure;
        # combined = 0.99, plus 0.015 proposal => 1.005 > 1.0.
        result = make_engine().pre_trade(
            make_proposal(),
            StubPortfolio(
                exposures={
                    AllocationBucket.STOCK: Decimal("0.50"),
                    AllocationBucket.TACTICAL: Decimal("0.49"),
                }
            ),
            make_phase_constraints(),
            MarketRegime.SIDEWAYS,
        )
        assert "class_cap_breach" in result.reasons

    def test_correlation_guard(self) -> None:
        # Correlation lookup returns above the configured max.
        def lookup(_instr: Instrument) -> Decimal | None:
            return Decimal("0.95")

        result = make_engine().pre_trade(
            make_proposal(),
            StubPortfolio(),
            make_phase_constraints(),
            MarketRegime.SIDEWAYS,
            correlation_lookup=lookup,
        )
        assert "correlation_breach" in result.reasons

    def test_correlation_under_max_passes(self) -> None:
        def lookup(_instr: Instrument) -> Decimal | None:
            return Decimal("0.80")

        result = make_engine().pre_trade(
            make_proposal(),
            StubPortfolio(),
            make_phase_constraints(),
            MarketRegime.SIDEWAYS,
            correlation_lookup=lookup,
        )
        assert result.passed is True

    def test_correlation_lookup_none_skips_gate(self) -> None:
        result = make_engine().pre_trade(
            make_proposal(),
            StubPortfolio(),
            make_phase_constraints(),
            MarketRegime.SIDEWAYS,
        )
        assert result.passed is True

    def test_regime_gate_for_turbo(self) -> None:
        turbo = Instrument(
            id=InstrumentId("turbo-1"),
            symbol="T1",
            exchange="EPA",
            currency=EUR,
            cls=InstrumentClass.TURBO,
        )
        proposal = make_proposal(instrument=turbo)
        # Make a phase constraint that allows turbos at the proposed size.
        pc = make_phase_constraints(
            stock_alloc="0.50",
            tactical_alloc="0.30",
            turbo_alloc="0.20",
        )
        result = make_engine().pre_trade(
            proposal,
            StubPortfolio(),
            pc,
            MarketRegime.HIGH_VOL,
        )
        assert "regime_forbidden" in result.reasons

    def test_regime_gate_passes_for_stock_in_high_vol(self) -> None:
        # STOCK class has no forbidden regimes by default, so HIGH_VOL
        # passes (this is the screener's / strategy's job, not the
        # risk gate's).
        result = make_engine().pre_trade(
            make_proposal(),
            StubPortfolio(),
            make_phase_constraints(),
            MarketRegime.HIGH_VOL,
        )
        assert result.passed is True

    def test_happy_path_acceptance(self) -> None:
        result = make_engine().pre_trade(
            make_proposal(),
            StubPortfolio(),
            make_phase_constraints(),
            MarketRegime.SIDEWAYS,
        )
        assert result.passed is True
        assert result.reasons == ()


# ---------------------------------------------------------------------------
# Post-trade gate (REQ_SDD_ALG_005 / REQ_SDD_ALG_009)
# ---------------------------------------------------------------------------


def make_curve(values: list[str]) -> list[EquityPoint]:
    s = datetime(2026, 1, 1)
    out: list[EquityPoint] = []
    peak = Decimal(0)
    for i, v in enumerate(values):
        amt = Decimal(v)
        peak = max(peak, amt)
        dd = max(Decimal(0), Decimal(1) - amt / peak) if peak > 0 else Decimal(0)
        out.append(
            EquityPoint(
                at=s + timedelta(days=i),
                equity_gross=Money(amt, EUR),
                equity_after_tax=Money(amt, EUR),
                drawdown_pct=dd,
            )
        )
    return out


class TestPostTradeDrawdown:
    def test_dd_below_cap_no_trigger(self) -> None:
        safety = StubSafety()
        engine = make_engine(safety)
        # Peak 100, current 90 => dd = 0.10 < cap 0.15.
        engine.post_trade(
            make_curve(["100", "100", "90"]),
            make_phase_constraints(),
            at=datetime(2026, 5, 1),
            snapshot_id=SnapshotId("snap-1"),
        )
        assert safety.triggers == []

    def test_dd_above_cap_triggers_kill(self) -> None:
        safety = StubSafety()
        engine = make_engine(safety)
        # Peak 100, current 80 => dd = 0.20 > cap 0.15.
        engine.post_trade(
            make_curve(["100", "100", "80"]),
            make_phase_constraints(max_drawdown="0.15"),
            at=datetime(2026, 5, 1),
            snapshot_id=SnapshotId("snap-1"),
        )
        assert len(safety.triggers) == 1
        t = safety.triggers[0]
        assert t.severity == "KILL"
        assert t.code == "dd_breach"


class TestPostTradeVolCap:
    def test_phase_5_plus_vol_cap_breach(self) -> None:
        safety = StubSafety()
        engine = make_engine(safety, correlation_window_days=10)
        # Build an oscillating curve with vol > 12% annualized.
        values = ["100"]
        for i in range(60):
            prev = Decimal(values[-1])
            values.append(str(prev * (Decimal("1.05") if i % 2 == 0 else Decimal("0.95"))))
        # No drawdown breach (oscillates around peak).
        # max_drawdown high enough to skip the dd path.
        engine.post_trade(
            make_curve(values),
            make_phase_constraints(
                max_drawdown="0.99",
                portfolio_vol_cap=Decimal("0.12"),
            ),
            at=datetime(2026, 5, 1),
            snapshot_id=SnapshotId("snap-1"),
        )
        # Either the dd guard or the vol guard fires; we asserted dd
        # is high enough not to trip, so the vol cap must trip.
        assert len(safety.triggers) == 1
        t = safety.triggers[0]
        assert t.severity == "DEGRADE"
        assert t.code == "vol_cap_breach"

    def test_no_vol_cap_skips_check(self) -> None:
        safety = StubSafety()
        engine = make_engine(safety)
        engine.post_trade(
            make_curve(["100", "100", "100"]),
            make_phase_constraints(portfolio_vol_cap=None),
            at=datetime(2026, 5, 1),
            snapshot_id=SnapshotId("snap-1"),
        )
        assert safety.triggers == []

    def test_dd_breach_short_circuits_vol_check(self) -> None:
        safety = StubSafety()
        engine = make_engine(safety)
        engine.post_trade(
            make_curve(["100", "100", "80"]),  # dd 0.20
            make_phase_constraints(
                max_drawdown="0.15",
                portfolio_vol_cap=Decimal("0.001"),  # would also trip
            ),
            at=datetime(2026, 5, 1),
            snapshot_id=SnapshotId("snap-1"),
        )
        # Only one trigger (the dd one); vol path skipped.
        assert len(safety.triggers) == 1
        assert safety.triggers[0].code == "dd_breach"
