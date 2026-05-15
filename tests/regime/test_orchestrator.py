"""Tests for ``trading_system.regime.orchestrator.RegimeOrchestrator``.

Covers TC_RGM_008 (confirmed transition raises a `KillSwitchTrigger`
via the SafetyLayer — never `set_state` directly) and the persistence
follow-up (write through ``TransitionRepository``).

REQ refs: REQ_F_RGM_005, REQ_SDS_RGM_001, REQ_SDD_RGM_004,
REQ_SDD_RGM_005, REQ_S_KS_002, REQ_S_KS_008.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trading_system.data.types import Bar
from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, SnapshotId
from trading_system.models.phase import MarketRegime
from trading_system.models.safety import (
    KillSwitchState,
    KillSwitchTrigger,
    TriggerCategory,
)
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.transition import TransitionRepository
from trading_system.regime.config import RegimeConfig
from trading_system.regime.detector import RegimeDetector
from trading_system.regime.orchestrator import RegimeOrchestrator
from trading_system.regime.transition import TransitionTracker
from trading_system.result import Err, Nothing, Ok, Some

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


@dataclass(slots=True)
class FakeSafetyLayer:
    """Test double — captures every ``raise_trigger`` call. Never
    advances state (we only want to observe that the orchestrator
    funnels triggers through the SafetyLayer surface, not the
    `set_state` API)."""

    triggers: list[KillSwitchTrigger] = field(default_factory=list)
    _state: KillSwitchState = KillSwitchState.ACTIVE

    def must_halt(self) -> bool:
        return self._state == KillSwitchState.KILL

    def state(self) -> KillSwitchState:
        return self._state

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        self.triggers.append(trigger)


def _bars(closes: list[Decimal]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            at=start + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=Decimal(1000),
        )
        for i, c in enumerate(closes)
    ]


def _geometric(start: Decimal, factor: Decimal, n: int) -> list[Decimal]:
    out: list[Decimal] = []
    p = start
    for _ in range(n):
        out.append(p)
        p = p * factor
    return out


def _config() -> RegimeConfig:
    return RegimeConfig(
        ma_short=10,
        ma_long=30,
        vol_window=10,
        confirmation_periods=2,
    )


def _migrated_repo(tmp_path: Path) -> TransitionRepository:
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run()
    return TransitionRepository(conn=conn)


def _orchestrator(tmp_path: Path) -> tuple[RegimeOrchestrator, FakeSafetyLayer, TransitionRepository]:
    safety = FakeSafetyLayer()
    repo = _migrated_repo(tmp_path)
    cfg = _config()
    return (
        RegimeOrchestrator(
            detector=RegimeDetector(config=cfg),
            tracker=TransitionTracker(confirmation_periods=cfg.confirmation_periods),
            safety=safety,
            repo=repo,
            account_id=DEFAULT_ACCOUNT_ID,
        ),
        safety,
        repo,
    )


# ---------------------------------------------------------------------------
# Insufficient-history → Err passes through (no trigger, no persistence)
# ---------------------------------------------------------------------------


def test_insufficient_bars_returns_err_without_side_effects(tmp_path: Path) -> None:
    orch, safety, repo = _orchestrator(tmp_path)
    bars = _bars([Decimal("100")] * 5)  # < ma_long (30)
    res = orch.observe(
        bars,
        at=datetime(2026, 1, 5, tzinfo=UTC),
        snapshot_id=SnapshotId("snap-1"),
    )
    match res:
        case Err(reason):
            assert reason.startswith("regime:insufficient_bars:")
        case Ok(_):
            raise AssertionError("expected insufficient-bars Err")
    assert safety.triggers == []
    assert repo.history().unwrap() == ()


# ---------------------------------------------------------------------------
# Stable BULL → no trigger, no persistence row
# ---------------------------------------------------------------------------


def test_stable_regime_does_not_raise_trigger(tmp_path: Path) -> None:
    orch, safety, repo = _orchestrator(tmp_path)
    bars = _bars([Decimal("100") + Decimal("0.4") * Decimal(i) for i in range(50)])
    tick = orch.observe(
        bars,
        at=datetime(2026, 1, 1, tzinfo=UTC),
        snapshot_id=SnapshotId("snap-bull"),
    ).unwrap()
    assert tick.transition_raised is False
    assert tick.regime is MarketRegime.BULL
    assert safety.triggers == []
    assert repo.history().unwrap() == ()


# ---------------------------------------------------------------------------
# TC_RGM_008 — Confirmed transition raises a KillSwitchTrigger
# ---------------------------------------------------------------------------


def test_confirmed_transition_raises_strategy_degrade_trigger(tmp_path: Path) -> None:
    orch, safety, repo = _orchestrator(tmp_path)
    bull_bars = _bars([Decimal("100") + Decimal("0.4") * Decimal(i) for i in range(50)])
    bear_bars = _bars(_geometric(Decimal("120"), Decimal("0.995"), 50))

    # Tick 1: BULL is observed first — seeds the cursor; no trigger.
    t1 = orch.observe(
        bull_bars,
        at=datetime(2026, 1, 1, tzinfo=UTC),
        snapshot_id=SnapshotId("snap-bull"),
    ).unwrap()
    assert t1.regime is MarketRegime.BULL
    assert t1.transition_raised is False

    # Tick 2: BEAR observed once — candidate=BEAR, count=1; still no trigger.
    t2 = orch.observe(
        bear_bars,
        at=datetime(2026, 1, 2, tzinfo=UTC),
        snapshot_id=SnapshotId("snap-bear-1"),
    ).unwrap()
    assert t2.regime is MarketRegime.BEAR
    assert t2.transition_raised is False
    assert safety.triggers == []

    # Tick 3: BEAR again — confirmation window full; emit + raise + persist.
    t3 = orch.observe(
        bear_bars,
        at=datetime(2026, 1, 3, tzinfo=UTC),
        snapshot_id=SnapshotId("snap-bear-2"),
    ).unwrap()
    assert t3.regime is MarketRegime.BEAR
    assert t3.transition_raised is True

    # SafetyLayer received exactly one trigger with the documented shape.
    assert len(safety.triggers) == 1
    trig = safety.triggers[0]
    assert trig.category is TriggerCategory.STRATEGY
    assert trig.code == "regime_transition"
    assert trig.severity == "DEGRADE"
    assert "bull" in trig.message and "bear" in trig.message
    assert trig.snapshot_id == SnapshotId("snap-bear-2")

    # Persistence captured the event.
    history = repo.history().unwrap()
    assert len(history) == 1
    assert history[0].from_regime is MarketRegime.BULL
    assert history[0].to_regime is MarketRegime.BEAR


# ---------------------------------------------------------------------------
# REQ_S_KS_002 — orchestrator never bypasses SafetyLayer (no set_state)
# ---------------------------------------------------------------------------


def test_orchestrator_only_uses_raise_trigger_not_set_state(tmp_path: Path) -> None:
    """AST audit — the orchestrator module SHALL NOT reference
    ``KillSwitch.set_state``. Only ``SafetyLayer.raise_trigger`` is
    permitted (REQ_S_KS_002 / REQ_SDD_RGM_004)."""
    import ast

    src = Path(
        _REPO_ROOT, "trading_system", "regime", "orchestrator.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    set_state_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "set_state":
            set_state_calls.append(node)
    assert set_state_calls == [], (
        "RegimeOrchestrator must not call .set_state — kill-switch state "
        "transitions go through SafetyLayer per REQ_S_KS_002"
    )


# ---------------------------------------------------------------------------
# Current-regime accessor reflects the tracker cursor
# ---------------------------------------------------------------------------


def test_current_regime_accessor_reflects_tracker(tmp_path: Path) -> None:
    orch, _, _ = _orchestrator(tmp_path)
    # No observation yet — Nothing().
    assert isinstance(orch.current_regime(), Nothing)
    bull_bars = _bars([Decimal("100") + Decimal("0.4") * Decimal(i) for i in range(50)])
    orch.observe(
        bull_bars,
        at=datetime(2026, 1, 1, tzinfo=UTC),
        snapshot_id=SnapshotId("snap-bull"),
    )
    match orch.current_regime():
        case Some(r):
            assert r is MarketRegime.BULL
        case _:
            raise AssertionError("expected Some(BULL) after first observation")
