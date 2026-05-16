"""``SummaryPublisher`` — render-only digest builder (REQ_F_NOT_006 /
REQ_NF_NOT_002).

The publisher consumes read-only views of the existing
``Analytics`` / ``Portfolio`` / ``Registry`` modules through three
Protocols (so this package stays free of import-side coupling) and
emits a frozen ``Summary`` payload. The output is canonically
serialisable via ``canonical.canonical_json_line`` — identical
inputs SHALL produce byte-identical JSON strings.

Scheduling (when to call ``render``) is out of scope here — Phase B
adds a scheduler under ``main.py`` or a Phase-6 cron-style helper.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId
from trading_system.models.instrument import InstrumentClass
from trading_system.models.money import Money
from trading_system.notifications.payloads import (
    RealizationLine,
    Summary,
    SummarySchedule,
)


@runtime_checkable
class PortfolioReader(Protocol):
    """Minimal read-only Portfolio surface the publisher needs."""

    def equity_after_tax(
        self, *, account_id: AccountId, as_of: datetime
    ) -> Money: ...


@runtime_checkable
class AnalyticsReader(Protocol):
    """Minimal read-only Analytics surface."""

    def exposure_by_class(
        self, *, account_id: AccountId, as_of: datetime
    ) -> Mapping[InstrumentClass, Decimal]: ...

    def top_realizations(
        self, *, account_id: AccountId, as_of: datetime, n: int = 5
    ) -> tuple[RealizationLine, ...]: ...


@runtime_checkable
class RegistryReader(Protocol):
    """Minimal read-only registry surface."""

    def last_improvement_digest(
        self, *, account_id: AccountId, as_of: datetime
    ) -> str: ...

    def pending_milestones(
        self, *, account_id: AccountId, as_of: datetime
    ) -> tuple[str, ...]: ...


@dataclass(slots=True)
class SummaryPublisher:
    """Render-only — does NOT publish anywhere itself.

    The caller wires the rendered Summary into a NotificationFanOut.
    Keeping render + publish separate means snapshot-test driven
    determinism verification stays trivial (just compare two
    canonical-JSON strings).
    """

    portfolio: PortfolioReader
    analytics: AnalyticsReader
    registry: RegistryReader

    def render(
        self,
        *,
        schedule: SummarySchedule,
        account_id: AccountId,
        as_of: datetime,
    ) -> Summary:
        return Summary(
            schedule=schedule,
            account_id=account_id,
            as_of=as_of,
            equity_after_tax=self.portfolio.equity_after_tax(
                account_id=account_id, as_of=as_of
            ),
            exposure=self.analytics.exposure_by_class(
                account_id=account_id, as_of=as_of
            ),
            top_realizations=self.analytics.top_realizations(
                account_id=account_id, as_of=as_of
            ),
            pending_milestones=self.registry.pending_milestones(
                account_id=account_id, as_of=as_of
            ),
            last_improvement_digest=self.registry.last_improvement_digest(
                account_id=account_id, as_of=as_of
            ),
        )
