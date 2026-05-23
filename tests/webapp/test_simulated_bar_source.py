"""Tests for ``SimulatedBarSource`` + ``PaperTickDriver``.

REQ refs:
- REQ_F_PAP_001 — BarSource Protocol satisfied by the simulator.
- REQ_NF_DET_001 — same seed ⇒ identical bar sequence.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

# The PaperTickDriver loop sleeps via ``asyncio.sleep`` to advance
# its internal cadence — the conformance ``test_clock_discipline``
# audit flags any test file that uses sleep without the
# ``wallclock`` marker (REQ_TP_FIX_001).
pytestmark = pytest.mark.wallclock

from trading_system.models.identifiers import AccountId, InstrumentId, StrategyId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.phase import (
    AllocationBucket,
    MarketRegime,
    PhaseConstraints,
)
from trading_system.result import Nothing, Ok, Some
from trading_system.webapp.runtimes.paper_trading import (
    PAPER_ACCOUNT_PREFIX,
    PaperTradingSession,
    RuntimeRegistry,
    build_runtime,
)
from trading_system.webapp.runtimes.simulated_bar_source import (
    SimulatedBarSource,
)
from trading_system.webapp.runtimes.tick_driver import PaperTickDriver


# ---------------------------------------------------------------------------
# SimulatedBarSource — determinism + happy path
# ---------------------------------------------------------------------------


def test_simulated_bar_source_next_bar_returns_some() -> None:
    src = SimulatedBarSource(instrument_id=InstrumentId("ASML.AS"))
    result = src.next_bar()
    assert isinstance(result, Ok)
    assert isinstance(result.value, Some)
    bar = result.value.value
    assert bar.close > 0
    assert bar.high >= bar.close
    assert bar.low <= bar.close


def test_simulated_bar_source_is_deterministic_for_same_seed() -> None:
    """REQ_NF_DET_001 — same seed + same call sequence ⇒
    identical bar streams."""
    start = datetime(2026, 5, 22, 9, 0, tzinfo=UTC)
    a = SimulatedBarSource(
        instrument_id=InstrumentId("ASML.AS"), seed=42, start_at=start
    )
    b = SimulatedBarSource(
        instrument_id=InstrumentId("ASML.AS"), seed=42, start_at=start
    )
    bars_a = [a.next_bar().unwrap().unwrap() for _ in range(5)]
    bars_b = [b.next_bar().unwrap().unwrap() for _ in range(5)]
    assert bars_a == bars_b


def test_simulated_bar_source_distinct_seeds_diverge() -> None:
    start = datetime(2026, 5, 22, 9, 0, tzinfo=UTC)
    a = SimulatedBarSource(
        instrument_id=InstrumentId("ASML.AS"), seed=1, start_at=start
    )
    b = SimulatedBarSource(
        instrument_id=InstrumentId("ASML.AS"), seed=2, start_at=start
    )
    bars_a = [a.next_bar().unwrap().unwrap() for _ in range(5)]
    bars_b = [b.next_bar().unwrap().unwrap() for _ in range(5)]
    assert bars_a != bars_b


def test_latest_cached_returns_nothing_before_first_next_bar() -> None:
    src = SimulatedBarSource(instrument_id=InstrumentId("ASML.AS"))
    cached = src.latest_cached()
    assert isinstance(cached, Ok)
    assert isinstance(cached.value, Nothing)


def test_latest_cached_returns_most_recent_after_next_bar() -> None:
    src = SimulatedBarSource(instrument_id=InstrumentId("ASML.AS"))
    bar = src.next_bar().unwrap().unwrap()
    cached = src.latest_cached().unwrap().unwrap()
    assert cached == bar


def test_simulated_bar_source_rejects_bad_config() -> None:
    with pytest.raises(ValueError, match="step_seconds"):
        SimulatedBarSource(instrument_id=InstrumentId("X"), step_seconds=0)
    with pytest.raises(ValueError, match="base_price"):
        SimulatedBarSource(
            instrument_id=InstrumentId("X"), base_price=Decimal("0")
        )


def test_simulated_bar_source_advances_clock_by_step_seconds() -> None:
    start = datetime(2026, 5, 22, 9, 0, tzinfo=UTC)
    src = SimulatedBarSource(
        instrument_id=InstrumentId("X"), step_seconds=60, start_at=start
    )
    bar_a = src.next_bar().unwrap().unwrap()
    bar_b = src.next_bar().unwrap().unwrap()
    assert (bar_b.at - bar_a.at).total_seconds() == 60


# ---------------------------------------------------------------------------
# PaperTickDriver — start / drain / stop
# ---------------------------------------------------------------------------


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.EUR)


def _stock() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _constraints() -> PhaseConstraints:
    return PhaseConstraints(
        max_positions=3,
        max_trades_per_month=4,
        allocation_targets={
            AllocationBucket.STOCK: Decimal("0.90"),
            AllocationBucket.TACTICAL: Decimal("0.10"),
        },
        turbo_exposure_max=Decimal("0"),
        risk_per_trade_band=(Decimal("0.01"), Decimal("0.02")),
        max_drawdown=Decimal("0.15"),
    )


class _NoopStrategy:
    id = StrategyId("noop")

    def evaluate(self, _state) -> list:
        return []


def _make_runtime():
    aid = AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-22T12:00:00+00:00")
    session = PaperTradingSession(
        account_id=aid,
        universe="eu-dividend-starter",
        strategy_id=StrategyId("noop"),
        starting_capital=_eur("10000"),
        started_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
    )
    src = SimulatedBarSource(
        instrument_id=_stock().id,
        seed=7,
        start_at=datetime(2026, 5, 22, 9, 0, tzinfo=UTC),
    )
    res = build_runtime(
        session=session,
        instrument=_stock(),
        strategy=_NoopStrategy(),  # type: ignore[arg-type]
        bar_source=src,
        phase_constraints=_constraints(),
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok)
    return res.value


def test_tick_driver_rejects_bad_interval() -> None:
    with pytest.raises(ValueError, match="interval_seconds"):
        PaperTickDriver(registry=RuntimeRegistry(), interval_seconds=0)


@pytest.mark.asyncio
async def test_tick_driver_runs_a_runtime_tick() -> None:
    """The driver SHALL invoke ``tick_once`` on every registered
    runtime per ``interval_seconds``."""
    registry = RuntimeRegistry()
    runtime = _make_runtime()
    registry.start(runtime).unwrap()
    driver = PaperTickDriver(registry=registry, interval_seconds=0.01)
    driver.start()
    try:
        # Give the loop a few ticks worth of wall-clock.
        await asyncio.sleep(0.1)
    finally:
        await driver.stop()
    # At least one equity point should have landed.
    assert len(runtime.equity_history()) >= 1


@pytest.mark.asyncio
async def test_tick_driver_stop_is_idempotent() -> None:
    registry = RuntimeRegistry()
    driver = PaperTickDriver(registry=registry, interval_seconds=0.01)
    driver.start()
    await driver.stop()
    # Calling stop again must not raise.
    await driver.stop()


@pytest.mark.asyncio
async def test_tick_driver_throttles_repeated_error_logs(caplog) -> None:  # type: ignore[no-untyped-def]
    """A runtime stuck in a transient-error loop SHALL re-log the
    same (account_id, error) at most once per _LOG_COOLDOWN_SECONDS
    so the logs don't spam at the tick cadence (2s)."""
    import logging

    from trading_system.result import Err

    class _PersistentErrRegistry:
        """Returns the same erroring runtime on every sweep."""

        def __init__(self) -> None:
            class _R:
                def is_alive(self) -> bool:
                    return True

                def tick_once(self):
                    return Err("paper:persistent_err")

            self._r = _R()

        def live_account_ids(self):
            return (AccountId(f"{PAPER_ACCOUNT_PREFIX}log-spam"),)

        def status(self, account_id):
            del account_id
            return Some(self._r)

    driver = PaperTickDriver(
        registry=_PersistentErrRegistry(),  # type: ignore[arg-type]
        interval_seconds=0.01,
    )
    caplog.set_level(logging.WARNING)
    driver.start()
    await asyncio.sleep(0.1)  # ~10 ticks
    await driver.stop()
    # Many ticks happened; the spam guard SHALL collapse them
    # into at most one log entry within the cooldown window.
    matching = [
        rec
        for rec in caplog.records
        if "paper:persistent_err" in rec.getMessage()
    ]
    assert 1 <= len(matching) <= 2, (
        f"expected 1-2 throttled log entries, got {len(matching)}: "
        f"{[r.getMessage() for r in matching]}"
    )


@pytest.mark.asyncio
async def test_tick_driver_continues_after_runtime_err() -> None:
    """A tick that returns Err SHALL be logged but SHALL NOT
    crash the loop — the dashboard surfaces the degraded /
    stopped state through the paper-state reader instead."""

    class _ExplodingRegistry:
        """One-shot fake: returns a Some(runtime) whose tick_once
        always errs; the loop should log and continue."""

        def __init__(self) -> None:
            from trading_system.result import Err

            class _R:
                def is_alive(self) -> bool:
                    return True

                def tick_once(self):
                    return Err("paper:simulated_failure")

            self._r = _R()

        def live_account_ids(self) -> tuple[AccountId, ...]:
            return (AccountId(f"{PAPER_ACCOUNT_PREFIX}x"),)

        def status(self, account_id: AccountId):
            del account_id
            return Some(self._r)

    driver = PaperTickDriver(registry=_ExplodingRegistry(), interval_seconds=0.01)  # type: ignore[arg-type]
    driver.start()
    await asyncio.sleep(0.05)
    await driver.stop()
    # If we got here without raising, the loop survived the Err.
