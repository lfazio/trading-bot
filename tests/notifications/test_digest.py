"""Tests for ``SummaryPublisher`` render determinism
(REQ_F_NOT_006, REQ_NF_NOT_002, REQ_SDD_NOT_005)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.models.identifiers import AccountId, InstrumentId
from trading_system.models.instrument import InstrumentClass
from trading_system.models.money import Currency, Money
from trading_system.notifications.canonical import canonical_json_line
from trading_system.notifications.digest import (
    AnalyticsReader,
    PortfolioReader,
    RegistryReader,
    SummaryPublisher,
)
from trading_system.notifications.payloads import RealizationLine, Summary


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


@dataclass(slots=True)
class _StubPortfolio:
    equity: Money = Money(Decimal("10000"), Currency.EUR)

    def equity_after_tax(
        self, *, account_id: AccountId, as_of: datetime
    ) -> Money:
        return self.equity


@dataclass(slots=True)
class _StubAnalytics:
    exposure: Mapping[InstrumentClass, Decimal] = field(
        default_factory=lambda: {
            InstrumentClass.STOCK: Decimal("0.7"),
            InstrumentClass.CASH: Decimal("0.3"),
        }
    )
    realizations: tuple[RealizationLine, ...] = ()

    def exposure_by_class(
        self, *, account_id: AccountId, as_of: datetime
    ) -> Mapping[InstrumentClass, Decimal]:
        return self.exposure

    def top_realizations(
        self, *, account_id: AccountId, as_of: datetime, n: int = 5
    ) -> tuple[RealizationLine, ...]:
        return self.realizations


@dataclass(slots=True)
class _StubRegistry:
    digest: str = "core-strategy-v2"
    milestones: tuple[str, ...] = ()

    def last_improvement_digest(
        self, *, account_id: AccountId, as_of: datetime
    ) -> str:
        return self.digest

    def pending_milestones(
        self, *, account_id: AccountId, as_of: datetime
    ) -> tuple[str, ...]:
        return self.milestones


def _publisher() -> SummaryPublisher:
    return SummaryPublisher(
        portfolio=_StubPortfolio(),
        analytics=_StubAnalytics(),
        registry=_StubRegistry(),
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_stub_portfolio_satisfies_protocol() -> None:
    assert isinstance(_StubPortfolio(), PortfolioReader)


def test_stub_analytics_satisfies_protocol() -> None:
    assert isinstance(_StubAnalytics(), AnalyticsReader)


def test_stub_registry_satisfies_protocol() -> None:
    assert isinstance(_StubRegistry(), RegistryReader)


# ---------------------------------------------------------------------------
# Render happy path
# ---------------------------------------------------------------------------


def test_render_returns_summary() -> None:
    s = _publisher().render(
        schedule="daily",
        account_id=AccountId("alpha"),
        as_of=_NOW,
    )
    assert isinstance(s, Summary)
    assert s.schedule == "daily"
    assert s.account_id == AccountId("alpha")
    assert s.equity_after_tax.amount == Decimal("10000")
    assert s.exposure[InstrumentClass.STOCK] == Decimal("0.7")
    assert s.last_improvement_digest == "core-strategy-v2"


def test_render_with_top_realizations() -> None:
    line = RealizationLine(
        instrument=InstrumentId("ASML.AS"),
        realized_after_tax=Money(Decimal("250"), Currency.EUR),
        closed_at=_NOW,
    )
    pub = SummaryPublisher(
        portfolio=_StubPortfolio(),
        analytics=_StubAnalytics(realizations=(line,)),
        registry=_StubRegistry(),
    )
    s = pub.render(
        schedule="weekly", account_id=AccountId("alpha"), as_of=_NOW
    )
    assert s.top_realizations == (line,)


# ---------------------------------------------------------------------------
# Determinism — REQ_NF_NOT_002
# ---------------------------------------------------------------------------


def test_two_renders_produce_identical_summary() -> None:
    """Two render calls against the same readers + same inputs SHALL
    produce equal Summary rows (REQ_NF_NOT_002)."""
    pub = _publisher()
    a = pub.render(
        schedule="daily", account_id=AccountId("alpha"), as_of=_NOW
    )
    b = pub.render(
        schedule="daily", account_id=AccountId("alpha"), as_of=_NOW
    )
    assert a == b


def test_two_renders_produce_byte_identical_canonical_json() -> None:
    """The fan-out's audit log relies on canonical_json_line being
    byte-identical for replays."""
    pub = _publisher()
    a = pub.render(
        schedule="daily", account_id=AccountId("alpha"), as_of=_NOW
    )
    b = pub.render(
        schedule="daily", account_id=AccountId("alpha"), as_of=_NOW
    )
    assert canonical_json_line(a) == canonical_json_line(b)


def test_distinct_schedules_produce_distinct_summaries() -> None:
    pub = _publisher()
    daily = pub.render(
        schedule="daily", account_id=AccountId("alpha"), as_of=_NOW
    )
    weekly = pub.render(
        schedule="weekly", account_id=AccountId("alpha"), as_of=_NOW
    )
    assert daily != weekly
