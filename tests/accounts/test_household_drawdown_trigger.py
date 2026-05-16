"""Tests for ``trading_system.accounts.household_drawdown_trigger``.

Covers TC_ACC_008 (household drawdown trigger DEGRADE / KILL
thresholds).

REQ refs: REQ_F_ACC_009, REQ_SDD_ACC_006, REQ_S_KS_003, REQ_S_KS_008.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.accounts.account import Account
from trading_system.accounts.group import PortfolioGroup
from trading_system.accounts.household_drawdown_trigger import (
    HouseholdDrawdownTrigger,
)
from trading_system.accounts.registry import AccountRegistry
from trading_system.accounts.tax_model import FranceCTOTaxModel
from trading_system.models.identifiers import AccountId, SnapshotId
from trading_system.models.money import Currency, Money
from trading_system.models.safety import TriggerCategory
from trading_system.result import Err, Nothing, Ok, Some


@dataclass(slots=True)
class _PortfolioStub:
    equity_amount: Decimal = Decimal("100000")
    drawdown: Decimal = Decimal(0)

    def equity(self) -> Money:
        return Money(self.equity_amount, Currency.EUR)

    def positions_by_instrument(self):
        return ()

    def drawdown_pct(self) -> Decimal:
        return self.drawdown


def _account(account_id: str, *, drawdown: str) -> Account:
    return Account(
        id=AccountId(account_id),
        broker=object(),
        portfolio=_PortfolioStub(drawdown=Decimal(drawdown)),
        capital_flow=object(),
        phase_engine=object(),
        tax_model=FranceCTOTaxModel(),
        risk_overlay=object(),
        operator_token_account_id=account_id,
    )


def _group(drawdown_per_account: dict[str, str]) -> PortfolioGroup:
    registry = AccountRegistry()
    for account_id, drawdown in drawdown_per_account.items():
        registry.add(_account(account_id, drawdown=drawdown))
    return PortfolioGroup(registry=registry, base_currency=Currency.EUR)


_AT = datetime(2026, 5, 16, 10, 0, tzinfo=UTC)
_SNAPSHOT_ID = SnapshotId("snap-household-drawdown")


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_degrade_pct_must_lie_in_open_unit_interval() -> None:
    group = _group({"alpha": "0"})
    with pytest.raises(ValueError, match="degrade_pct"):
        HouseholdDrawdownTrigger(group=group, degrade_pct=Decimal(0))
    with pytest.raises(ValueError, match="degrade_pct"):
        HouseholdDrawdownTrigger(group=group, degrade_pct=Decimal("1.5"))


def test_degrade_pct_must_be_lt_kill_pct() -> None:
    group = _group({"alpha": "0"})
    with pytest.raises(ValueError, match="degrade_pct"):
        HouseholdDrawdownTrigger(
            group=group,
            degrade_pct=Decimal("0.20"),
            kill_pct=Decimal("0.15"),
        )


# ---------------------------------------------------------------------------
# TC_ACC_008 — DEGRADE / KILL thresholds
# ---------------------------------------------------------------------------


def test_below_degrade_returns_nothing() -> None:
    trigger = HouseholdDrawdownTrigger(
        group=_group({"alpha": "0.05", "beta": "0.08"}),
        degrade_pct=Decimal("0.12"),
        kill_pct=Decimal("0.15"),
    )
    match trigger.evaluate(at=_AT, snapshot_id=_SNAPSHOT_ID):
        case Ok(Nothing()):
            pass
        case other:
            raise AssertionError(f"expected Ok(Nothing()), got {other!r}")


def test_at_or_above_degrade_emits_degrade_trigger() -> None:
    # Account drawdown 0.12 = degrade_pct → DEGRADE (>=).
    trigger = HouseholdDrawdownTrigger(
        group=_group({"alpha": "0.12"}),
        degrade_pct=Decimal("0.12"),
        kill_pct=Decimal("0.20"),
    )
    match trigger.evaluate(at=_AT, snapshot_id=_SNAPSHOT_ID):
        case Ok(Some(event)):
            assert event.category is TriggerCategory.FINANCIAL
            assert event.severity == "DEGRADE"
            assert event.code == "financial:household_drawdown:degrade"
            assert "0.12" in event.message
        case other:
            raise AssertionError(f"expected DEGRADE trigger, got {other!r}")


def test_at_or_above_kill_emits_kill_trigger() -> None:
    # Account drawdown 0.20 > kill_pct → KILL pre-empts DEGRADE.
    trigger = HouseholdDrawdownTrigger(
        group=_group({"alpha": "0.20"}),
        degrade_pct=Decimal("0.12"),
        kill_pct=Decimal("0.15"),
    )
    match trigger.evaluate(at=_AT, snapshot_id=_SNAPSHOT_ID):
        case Ok(Some(event)):
            assert event.severity == "KILL"
            assert event.code == "financial:household_drawdown:kill"
        case other:
            raise AssertionError(f"expected KILL trigger, got {other!r}")


def test_kill_preempts_degrade_at_kill_threshold() -> None:
    """Exactly at kill_pct — KILL wins."""
    trigger = HouseholdDrawdownTrigger(
        group=_group({"alpha": "0.15"}),
        degrade_pct=Decimal("0.12"),
        kill_pct=Decimal("0.15"),
    )
    match trigger.evaluate(at=_AT, snapshot_id=_SNAPSHOT_ID):
        case Ok(Some(event)):
            assert event.severity == "KILL"
        case other:
            raise AssertionError(f"expected KILL trigger, got {other!r}")


def test_uses_max_drawdown_across_accounts() -> None:
    """One account exceeds the threshold — the trigger fires on the
    household conservatism: the worst account triggers everyone."""
    trigger = HouseholdDrawdownTrigger(
        group=_group({"alpha": "0.05", "beta": "0.13", "gamma": "0.08"}),
        degrade_pct=Decimal("0.12"),
        kill_pct=Decimal("0.20"),
    )
    match trigger.evaluate(at=_AT, snapshot_id=_SNAPSHOT_ID):
        case Ok(Some(event)):
            assert event.severity == "DEGRADE"
            assert "0.13" in event.message
        case other:
            raise AssertionError(f"expected DEGRADE trigger, got {other!r}")
