"""Paper-trading state reader — Protocol + RuntimeRegistry-backed concrete.

REQ refs:
- REQ_F_WEB2_003 — paper-trading dashboard panel reads a state
  snapshot per registered paper session.
- REQ_NF_WEB2_001 — read-side determinism: equal inputs ⇒
  byte-identical canonical JSON. The reader is a pure function
  of its inputs at any given moment in time.
- REQ_SDD_FAS_001 — closed import graph. The reader uses a
  Protocol-shaped slot (``PaperRuntimeView``) so the webapp does
  not import the concrete ``PaperTradingRuntime`` at this layer.

Pattern mirrors ``state_readers.py``: the webapp's lifespan
attaches a ``RuntimeRegistry`` to ``app.state``; the SSE handler
asks the reader for an async stream of snapshots, and the
request-response handler asks it for a single snapshot.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId
from trading_system.result import Some
from trading_system.webui.schemas import PaperStateResponse


@runtime_checkable
class PaperRuntimeView(Protocol):
    """Read-only surface a paper-trading runtime SHALL expose for
    the dashboard panel. The concrete ``PaperTradingRuntime`` from
    ``trading_system.webapp.runtimes.paper_trading`` satisfies
    this Protocol structurally; tests inject hand-rolled stubs."""

    def is_alive(self) -> bool: ...
    def is_degraded(self) -> bool: ...
    def degraded_since(self) -> datetime | None: ...
    def last_tick_at(self) -> datetime | None: ...
    def equity_history(self) -> tuple: ...  # tuple[EquityPoint, ...]


@runtime_checkable
class PaperRegistryView(Protocol):
    """Read-only surface ``RuntimeRegistry`` exposes — just the
    one lookup the reader needs. Lets tests inject a fake
    registry without dragging in the live-ticking surface."""

    def status(self, account_id: AccountId): ...  # returns Option[runtime]


@dataclass(frozen=True, slots=True)
class RuntimePaperStateReader:
    """Concrete ``PaperStateReader`` over a ``RuntimeRegistry``.

    Construct via the webapp's ``default_app()``; tests construct
    directly with a fake registry. The ``tick_seconds`` parameter
    sets the SSE push cadence (default 2s — the paper panel needs
    to feel live; the existing 5s live-state cadence is for the
    aggregate dashboard).
    """

    registry: PaperRegistryView
    tick_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.tick_seconds <= 0:
            raise ValueError(
                "RuntimePaperStateReader.tick_seconds must be > 0, "
                f"got {self.tick_seconds}"
            )

    def paper_state(
        self, *, account_id: AccountId, as_of: datetime
    ) -> PaperStateResponse:
        """Snapshot for one paper-trading session.

        Returns the documented "session_not_found" sentinel (an
        all-zeroed payload with ``is_alive=False``) when the
        registry has no live entry for the requested account_id
        — keeps the SSE stream contract single-shape so HTMX
        doesn't need a separate error path.
        """
        runtime_opt = self.registry.status(account_id)
        if not isinstance(runtime_opt, Some):
            return PaperStateResponse(
                account_id=account_id,
                as_of=as_of,
                is_alive=False,
                is_degraded=False,
                degraded_since=None,
                last_tick_at=None,
                equity_points_count=0,
                latest_equity_after_tax=None,
            )
        runtime = runtime_opt.value
        history = runtime.equity_history()
        if history:
            latest_amount: Decimal | None = history[-1].equity_after_tax.amount
        else:
            latest_amount = None
        return PaperStateResponse(
            account_id=account_id,
            as_of=as_of,
            is_alive=runtime.is_alive(),
            is_degraded=runtime.is_degraded(),
            degraded_since=runtime.degraded_since(),
            last_tick_at=runtime.last_tick_at(),
            equity_points_count=len(history),
            latest_equity_after_tax=latest_amount,
        )

    async def subscribe(
        self, *, account_id: AccountId
    ) -> AsyncIterator[PaperStateResponse]:
        """Yield one snapshot every ``tick_seconds``.

        The handler exits the loop when the request disconnects
        (the SSE router checks ``request.is_disconnected()`` and
        breaks out of ``async for`` on the first ``True``).
        """
        while True:
            yield self.paper_state(
                account_id=account_id, as_of=datetime.now(tz=UTC)
            )
            await asyncio.sleep(self.tick_seconds)
