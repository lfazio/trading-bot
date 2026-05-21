"""Tests for ``trading_system.webapp.state_readers``.

REQ refs:
- REQ_F_FAS_001 / REQ_F_WEB_002 — the reader implements the
  Protocol surface the route layer consumes.
- REQ_NF_FAS_001 / REQ_NF_WEB_002 — equal inputs ⇒ equal output.
- REQ_SDD_FAS_001 — closed import graph; the reader reaches the
  runtime state via Protocols, not concrete types. Verified by
  the fact that this test file builds an in-process reader from
  primitives + Money / Decimal — never importing
  ``trading_system.portfolio.Portfolio`` or
  ``trading_system.safety.StateManager`` directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import AccountId
from trading_system.models.phase import Phase
from trading_system.models.safety import KillSwitchState
from trading_system.webapp.state_readers import (
    PortfolioStateView,
    RuntimeLiveStateReader,
    RuntimeStateBag,
    SafetyStateView,
    PhaseStateView,
)


_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# In-test Protocol satisfiers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StubMoney:
    amount: Decimal


@dataclass(frozen=True, slots=True)
class _StubPortfolio:
    """Satisfies ``PortfolioStateView`` structurally."""

    equity_amount: Decimal
    positions_count: int = 0

    def equity_after_tax(self) -> _StubMoney:
        return _StubMoney(amount=self.equity_amount)

    def positions(self) -> dict[str, str]:
        return {f"p{i}": f"v{i}" for i in range(self.positions_count)}


@dataclass(slots=True)
class _StubSafety:
    """Satisfies ``SafetyStateView`` structurally."""

    state: KillSwitchState = KillSwitchState.ACTIVE


@dataclass(frozen=True, slots=True)
class _StubPhase:
    """Satisfies ``PhaseStateView`` structurally."""

    phase: Phase = Phase.ONE

    def current(self) -> Phase:
        return self.phase


# ---------------------------------------------------------------------------
# Protocol conformance — the stubs are structurally valid
# ---------------------------------------------------------------------------


def test_stub_portfolio_satisfies_portfolio_state_view_protocol() -> None:
    assert isinstance(_StubPortfolio(equity_amount=Decimal("0")), PortfolioStateView)


def test_stub_safety_satisfies_safety_state_view_protocol() -> None:
    assert isinstance(_StubSafety(), SafetyStateView)


def test_stub_phase_satisfies_phase_state_view_protocol() -> None:
    assert isinstance(_StubPhase(), PhaseStateView)


# ---------------------------------------------------------------------------
# Empty bag — bootstrap defaults
# ---------------------------------------------------------------------------


def test_empty_bag_returns_bootstrap_defaults() -> None:
    bag = RuntimeStateBag(
        bootstrap_equity_after_tax=Decimal("12500.00"),
    )
    snap = bag.snapshot(account_id=AccountId("alpha"), as_of=_NOW)
    assert snap.account_id == "alpha"
    assert snap.as_of == _NOW
    assert snap.ks_state == KillSwitchState.ACTIVE
    assert snap.phase == Phase.ONE
    assert snap.open_positions_count == 0
    assert snap.equity_after_tax == Decimal("12500.00")


def test_empty_bag_with_zero_equity_renders_zero() -> None:
    """With no portfolio attached AND default bootstrap_equity_after_tax,
    the snapshot reports zero equity — no fake fallback."""
    bag = RuntimeStateBag()
    snap = bag.snapshot(account_id=AccountId("default"), as_of=_NOW)
    assert snap.equity_after_tax == Decimal("0")


# ---------------------------------------------------------------------------
# Populated bag — reads from attached views
# ---------------------------------------------------------------------------


def test_portfolio_view_drives_equity_and_position_count() -> None:
    portfolio = _StubPortfolio(equity_amount=Decimal("87654.32"), positions_count=3)
    bag = RuntimeStateBag(portfolio=portfolio)
    snap = bag.snapshot(account_id=AccountId("alpha"), as_of=_NOW)
    assert snap.equity_after_tax == Decimal("87654.32")
    assert snap.open_positions_count == 3


def test_safety_view_drives_ks_state() -> None:
    bag = RuntimeStateBag(safety=_StubSafety(state=KillSwitchState.DEGRADED))
    snap = bag.snapshot(account_id=AccountId("alpha"), as_of=_NOW)
    assert snap.ks_state == KillSwitchState.DEGRADED


def test_phase_view_drives_phase() -> None:
    bag = RuntimeStateBag(phase_engine=_StubPhase(phase=Phase.FOUR))
    snap = bag.snapshot(account_id=AccountId("alpha"), as_of=_NOW)
    assert snap.phase == Phase.FOUR


def test_full_bag_yields_full_real_snapshot() -> None:
    bag = RuntimeStateBag(
        portfolio=_StubPortfolio(equity_amount=Decimal("1234567"), positions_count=7),
        safety=_StubSafety(state=KillSwitchState.KILL),
        phase_engine=_StubPhase(phase=Phase.SIX),
    )
    snap = bag.snapshot(account_id=AccountId("household"), as_of=_NOW)
    assert snap.account_id == "household"
    assert snap.equity_after_tax == Decimal("1234567")
    assert snap.open_positions_count == 7
    assert snap.ks_state == KillSwitchState.KILL
    assert snap.phase == Phase.SIX


# ---------------------------------------------------------------------------
# Determinism — REQ_NF_WEB_002 byte-identical replay
# ---------------------------------------------------------------------------


def test_snapshot_is_deterministic_for_equal_inputs() -> None:
    bag = RuntimeStateBag(
        portfolio=_StubPortfolio(equity_amount=Decimal("100"), positions_count=2),
        safety=_StubSafety(state=KillSwitchState.ACTIVE),
        phase_engine=_StubPhase(phase=Phase.TWO),
    )
    a = bag.snapshot(account_id=AccountId("alpha"), as_of=_NOW)
    b = bag.snapshot(account_id=AccountId("alpha"), as_of=_NOW)
    # Frozen dataclass equality — structural.
    assert a == b
    # And the canonical-JSON serialisation is byte-identical.
    assert a.render_canonical() == b.render_canonical()


# ---------------------------------------------------------------------------
# RuntimeLiveStateReader — Protocol satisfaction + subscribe()
# ---------------------------------------------------------------------------


def test_reader_implements_live_state() -> None:
    reader = RuntimeLiveStateReader(bag=RuntimeStateBag())
    snap = reader.live_state(account_id=AccountId("default"), as_of=_NOW)
    assert snap.account_id == "default"


def test_reader_rejects_non_positive_tick_seconds() -> None:
    with pytest.raises(ValueError, match="tick_seconds must be > 0"):
        RuntimeLiveStateReader(bag=RuntimeStateBag(), tick_seconds=0)
    with pytest.raises(ValueError, match="tick_seconds must be > 0"):
        RuntimeLiveStateReader(bag=RuntimeStateBag(), tick_seconds=-1.5)


def test_reader_subscribe_yields_snapshots() -> None:
    """``subscribe`` is an async iterator that yields once per tick.
    Use a tiny tick + ``asyncio.wait_for`` to grab two snapshots
    deterministically without sitting on a real 5-second timer."""
    reader = RuntimeLiveStateReader(
        bag=RuntimeStateBag(
            portfolio=_StubPortfolio(equity_amount=Decimal("9999")),
        ),
        tick_seconds=0.01,
    )

    async def collect_two():  # type: ignore[no-untyped-def]
        out = []
        async for snap in reader.subscribe(account_id=AccountId("alpha")):
            out.append(snap)
            if len(out) >= 2:
                break
        return out

    snaps = asyncio.run(asyncio.wait_for(collect_two(), timeout=1.0))
    assert len(snaps) == 2
    assert all(s.equity_after_tax == Decimal("9999") for s in snaps)
    # Two ticks with non-decreasing as_of timestamps.
    assert snaps[1].as_of >= snaps[0].as_of


# Pytest mark — subscribe() uses asyncio.sleep which is a wall-clock
# dependency; mark the file so REQ_TP_FIX_001's audit accepts it.
pytestmark = pytest.mark.wallclock
