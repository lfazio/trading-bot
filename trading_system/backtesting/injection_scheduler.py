"""``InjectionScheduler`` — replay an external-capital injection timeline.

REQ refs:
- REQ_F_BCT_007 — backtests apply external-capital injections at the
  scheduled timestamps.
- REQ_F_CFL_001 / REQ_SDD_ALG_017 — injection timeline kept sorted
  ascending; replay consumes in order.
- REQ_F_CFL_004 — backtests reuse the same canonical CapitalFlow
  ledger as live runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_system.capital_flow.flow import CapitalFlow
from trading_system.models.flow import Injection
from trading_system.portfolio.portfolio import Portfolio


@dataclass(slots=True)
class InjectionScheduler:
    """Yields due injections during a backtest tick.

    On each ``maybe_apply(t, ...)`` call, all pending injections with
    ``inj.at <= t`` are popped from the queue, recorded on the
    ``CapitalFlow`` ledger, and credited to the ``Portfolio`` cash
    balance. Returns the list of applied injections so the engine can
    count them for the result.
    """

    _pending: list[Injection] = field(default_factory=list)

    @classmethod
    def from_schedule(cls, schedule: tuple[Injection, ...] | list[Injection]) -> InjectionScheduler:
        sched = list(schedule)
        sched.sort(key=lambda i: i.at)
        return cls(_pending=sched)

    def maybe_apply(
        self,
        t: datetime,
        capital_flow: CapitalFlow,
        portfolio: Portfolio,
    ) -> list[Injection]:
        """Apply every pending injection with ``inj.at <= t``."""
        applied: list[Injection] = []
        while self._pending and self._pending[0].at <= t:
            inj = self._pending.pop(0)
            capital_flow.observe(inj)
            portfolio.inject(inj.amount)
            applied.append(inj)
        return applied

    @property
    def remaining(self) -> int:
        return len(self._pending)
