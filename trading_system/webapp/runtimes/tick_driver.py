"""Background tick-driver for the paper-trading runtime.

REQ refs:
- REQ_F_PAP_001 / REQ_F_PAP_005 — a single asyncio task drives
  every live runtime in the registry; one tick per
  ``interval_seconds`` per runtime.
- REQ_SDD_FAS_005 — the lifespan owns long-lived background
  resources; the driver is started by ``create_app``'s lifespan
  context and cancelled on shutdown.

The driver is intentionally simple — it sweeps the registry,
asks for each runtime's current snapshot, and invokes
``tick_once()`` on the live ones. ``tick_once`` itself is
synchronous (it composes the broker + portfolio engine pieces
which are sync) — wrapped in an executor here so the asyncio
loop stays responsive when a tick takes longer than its
budget (e.g., persistence flush).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from trading_system.result import Err, Some
from trading_system.webapp.runtimes.paper_trading import (
    PaperTradingRuntime,
    RuntimeRegistry,
)


_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class PaperTickDriver:
    """Asyncio loop that ticks every registered paper runtime."""

    registry: RuntimeRegistry
    interval_seconds: float = 2.0
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _stopped: asyncio.Event = field(
        default_factory=asyncio.Event, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError(
                f"PaperTickDriver.interval_seconds must be > 0, "
                f"got {self.interval_seconds}"
            )

    def start(self) -> None:
        """Start the loop. Idempotent — calling twice is a no-op."""
        if self._task is not None and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(
            self._loop(), name="paper-tick-driver"
        )

    async def stop(self) -> None:
        """Stop the loop + await the task's exit. Idempotent."""
        if self._task is None:
            return
        self._stopped.set()
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            # Cancellation is the happy path; any other error
            # was logged inside the loop.
            pass
        self._task = None

    async def _loop(self) -> None:
        """Repeatedly tick every live runtime."""
        try:
            while not self._stopped.is_set():
                for account_id in self.registry.live_account_ids():
                    opt = self.registry.status(account_id)
                    if isinstance(opt, Some):
                        runtime: PaperTradingRuntime = opt.value
                        result = runtime.tick_once()
                        if isinstance(result, Err):
                            # Log but don't crash the loop —
                            # one bad runtime SHALL NOT halt the
                            # others. The dashboard surfaces the
                            # degraded/stopped state through the
                            # paper-state reader.
                            _LOG.warning(
                                "paper_tick:%s:%s",
                                account_id,
                                result.error,
                            )
                try:
                    await asyncio.wait_for(
                        self._stopped.wait(),
                        timeout=self.interval_seconds,
                    )
                except asyncio.TimeoutError:
                    # Normal tick interval elapsed; loop continues.
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — last-resort
            _LOG.exception("paper-tick-driver crashed")
