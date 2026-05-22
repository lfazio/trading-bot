"""Live-state readers — Protocol-based bag + concrete runtime reader.

REQ refs:
- REQ_F_FAS_001 / REQ_F_WEB_002 — the ``LiveStateReader`` Protocol
  surface (defined in ``routers/api/live_state.py`` and ``sse.py``)
  is what the route layer consumes. This module ships the
  *concrete* implementation that pulls from a live trading
  process's state.
- REQ_NF_FAS_001 / REQ_NF_WEB_002 — equal inputs ⇒ byte-identical
  output. The reader is a pure function of its inputs; the only
  non-determinism comes from ``as_of`` which the caller supplies.
- REQ_SDD_FAS_001 — closed import graph. The reader reaches the
  runtime state via Protocols (``PortfolioStateView`` /
  ``SafetyStateView`` / ``PhaseStateView``) so the webapp does not
  import concrete portfolio/safety/phase types.

Architecture:

The trading loop and the webapp run in distinct processes
(REQ_NF_WEB_001 isolation). They share state via a
``RuntimeStateBag`` — a frozen dataclass holding *Protocol-shaped*
references to the live trading-loop instances. The trading-loop
side attaches the live objects at boot; the webapp side reads
through the bag at request time. When a field is ``None`` (no
trading process attached yet, or the operator booted the webapp
standalone for a quick poke), the reader falls back to the
documented bootstrap defaults: Phase 1, KS ACTIVE, zero open
positions, equity = ``starting_capital`` from config.

The bag is intentionally a *bag*, not a single mediator — Phase B
follow-ups will add ``AnalyticsView`` for live drawdown stats,
``CapitalFlowView`` for injection history, etc. Each new view
extends the bag without breaking existing callers.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId
from trading_system.models.phase import Phase
from trading_system.models.safety import KillSwitchState
from trading_system.webui.schemas import LiveStateResponse


# ---------------------------------------------------------------------------
# Protocol surfaces — what the reader needs from the trading loop
# ---------------------------------------------------------------------------


@runtime_checkable
class PortfolioStateView(Protocol):
    """Read-only portfolio surface — JUST what the dashboard needs.

    The concrete ``trading_system.portfolio.Portfolio`` satisfies
    this Protocol structurally (it has ``equity_after_tax()`` and
    ``positions()``). The webapp never imports the concrete type;
    operators wire any object satisfying the surface.
    """

    def equity_after_tax(self) -> object:
        """Return a ``Money``-like object with an ``.amount: Decimal``."""
        ...

    def positions(self) -> dict[object, object]:
        """Return the open-positions map; the reader uses ``len()`` only."""
        ...


@runtime_checkable
class SafetyStateView(Protocol):
    """Read-only safety surface — emits the current KS state.

    Declared as a method (not a property) to match the concrete
    ``trading_system.safety.state_manager.StateManager.state()``
    signature. Operators wiring a custom safety view SHALL expose
    ``state()`` as a callable that returns ``KillSwitchState``.
    """

    def state(self) -> KillSwitchState: ...


@runtime_checkable
class PhaseStateView(Protocol):
    """Read-only phase surface — emits the current ``Phase``."""

    def current(self) -> Phase: ...


# ---------------------------------------------------------------------------
# RuntimeStateBag — the shared state between trading loop and webapp
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuntimeStateBag:
    """Operator-injected bundle of live runtime views.

    Each field is *optional*: a fully-wired production deployment
    attaches all three to a live trading process; a standalone
    webapp boot (operator pokes around without a trading loop)
    leaves them ``None`` and the reader falls back to bootstrap
    defaults.

    The bag is frozen so subscribers see a consistent snapshot
    across the duration of one ``live_state`` call — mutating the
    underlying objects is still possible, but the bag's identity
    won't change mid-render. To swap which Portfolio/Safety/Phase
    the webapp reads from, construct a new bag and re-build the
    reader (operators do this from a small wiring script).
    """

    portfolio: PortfolioStateView | None = None
    safety: SafetyStateView | None = None
    phase_engine: PhaseStateView | None = None
    bootstrap_account_id: str = "default"
    bootstrap_phase: Phase = Phase.ONE
    bootstrap_ks_state: KillSwitchState = KillSwitchState.ACTIVE
    bootstrap_equity_after_tax: Decimal = Decimal("0")

    def snapshot(self, *, account_id: AccountId, as_of: datetime) -> LiveStateResponse:
        """Build a ``LiveStateResponse`` from the bag's current
        view references, falling back to bootstrap defaults for
        any unattached field.

        Pure function of the bag + (account_id, as_of) — equal
        inputs SHALL produce equal outputs (REQ_NF_WEB_002)."""
        if self.portfolio is not None:
            equity_money = self.portfolio.equity_after_tax()
            equity_amount = getattr(equity_money, "amount", self.bootstrap_equity_after_tax)
            positions_count = len(self.portfolio.positions())
        else:
            equity_amount = self.bootstrap_equity_after_tax
            positions_count = 0
        ks_state = (
            self.safety.state() if self.safety is not None else self.bootstrap_ks_state
        )
        phase = (
            self.phase_engine.current()
            if self.phase_engine is not None
            else self.bootstrap_phase
        )
        return LiveStateResponse(
            account_id=account_id,
            as_of=as_of,
            ks_state=ks_state,
            phase=phase,
            open_positions_count=positions_count,
            equity_after_tax=equity_amount,
        )


# ---------------------------------------------------------------------------
# RuntimeLiveStateReader — the Protocol-satisfying reader the webapp wires
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuntimeLiveStateReader:
    """Concrete ``LiveStateReader`` + ``LiveStateStreamReader``.

    Satisfies both the request-response (``live_state``) and
    streaming (``subscribe``) Protocols the webapp's route layer
    consumes. The streaming surface yields one snapshot per
    ``tick_seconds``; the SSE channel forwards each yield as a
    Server-Sent Event.

    The bag reference is captured at construction; tests and
    operators swap implementations by building a fresh reader
    wrapping a fresh bag — never by mutating an existing instance.
    """

    bag: RuntimeStateBag
    tick_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.tick_seconds <= 0:
            raise ValueError(
                f"RuntimeLiveStateReader.tick_seconds must be > 0, "
                f"got {self.tick_seconds}"
            )

    def live_state(
        self, *, account_id: AccountId, as_of: datetime
    ) -> LiveStateResponse:
        return self.bag.snapshot(account_id=account_id, as_of=as_of)

    async def subscribe(
        self, *, account_id: AccountId
    ) -> AsyncIterator[LiveStateResponse]:
        """Yield one snapshot every ``tick_seconds``. The reader
        re-queries the bag on each tick so a trading loop mutating
        the underlying Portfolio is visible without recreating the
        reader."""
        while True:
            yield self.bag.snapshot(
                account_id=account_id, as_of=datetime.now(tz=UTC)
            )
            await asyncio.sleep(self.tick_seconds)
