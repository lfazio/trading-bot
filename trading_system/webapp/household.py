"""Household snapshot — aggregate stats across every live paper session.

REQ refs:
- REQ_F_WEB2_008 — multi-account switcher surfaces a
  household-drawdown indicator when 2+ accounts are live so the
  operator sees risk across the household at a glance.

Pure-function design: takes the runtime registry + paper-state
reader Protocols and returns a small dataclass the dashboard
template renders. No I/O, no decisioning — just composition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

# AccountId is structurally consumed via the registry/reader
# Protocols; no concrete import needed.


@runtime_checkable
class HouseholdRegistryView(Protocol):
    """Minimal surface needed for the household roll-up."""

    def live_account_ids(self) -> tuple: ...  # tuple[AccountId, ...]


@runtime_checkable
class HouseholdReaderView(Protocol):
    """Subset of ``RuntimePaperStateReader`` used by the roll-up."""

    def paper_state(self, *, account_id, as_of):  # type: ignore[no-untyped-def]
        ...


@dataclass(frozen=True, slots=True)
class AccountSummary:
    """Compact per-account row consumed by the switcher."""

    account_id: str
    is_alive: bool
    equity_after_tax: Decimal | None
    drawdown_pct: Decimal | None
    instrument_symbol: str


@dataclass(frozen=True, slots=True)
class HouseholdSnapshot:
    """Aggregate across every live paper session.

    ``max_drawdown_pct`` is the worst drawdown across all
    sessions (not a true household-curve drawdown — that would
    need a netted equity curve, which is deferred until
    multi-account portfolio composition lands). Worst-of-N is
    the conservative bound and is enough to drive the
    operator-warning indicator.
    """

    account_count: int
    total_equity_after_tax: Decimal | None
    max_drawdown_pct: Decimal | None
    accounts: tuple[AccountSummary, ...]


def household_snapshot(
    registry: HouseholdRegistryView,
    reader: HouseholdReaderView,
    *,
    as_of: datetime,
) -> HouseholdSnapshot:
    """Walk the registry + reader to build the household roll-up."""
    try:
        account_ids = tuple(registry.live_account_ids())
    except Exception:  # noqa: BLE001 — defensive
        account_ids = ()
    accounts: list[AccountSummary] = []
    total: Decimal | None = None
    worst_dd: Decimal | None = None
    for aid in account_ids:
        try:
            snap = reader.paper_state(account_id=aid, as_of=as_of)
        except Exception:  # noqa: BLE001
            continue
        equity = getattr(snap, "latest_equity_after_tax", None)
        dd = getattr(snap, "drawdown_pct", None)
        accounts.append(
            AccountSummary(
                account_id=str(aid),
                is_alive=bool(getattr(snap, "is_alive", False)),
                equity_after_tax=equity,
                drawdown_pct=dd,
                instrument_symbol=str(
                    getattr(snap, "instrument_symbol", "") or ""
                ),
            )
        )
        if equity is not None:
            total = (total or Decimal("0")) + equity
        if dd is not None and (worst_dd is None or dd > worst_dd):
            worst_dd = dd
    return HouseholdSnapshot(
        account_count=len(accounts),
        total_equity_after_tax=total,
        max_drawdown_pct=worst_dd,
        accounts=tuple(accounts),
    )
