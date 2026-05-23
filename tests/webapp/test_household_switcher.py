"""Tests for the multi-account switcher + household-drawdown
indicator (REQ_F_WEB2_008)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.household import (
    AccountSummary,
    HouseholdSnapshot,
    household_snapshot,
)
from trading_system.webui.schemas import PaperStateResponse


_SECRET = b"household-secret"


@dataclass(slots=True)
class _FakeRegistry:
    ids: tuple[AccountId, ...] = ()

    def live_account_ids(self) -> tuple[AccountId, ...]:
        return self.ids


@dataclass(slots=True)
class _FakeReader:
    """Returns a pre-configured PaperStateResponse per account_id."""

    responses: dict[AccountId, PaperStateResponse] = field(default_factory=dict)

    def paper_state(self, *, account_id: AccountId, as_of: datetime):
        return self.responses.get(
            account_id,
            PaperStateResponse(
                account_id=account_id,
                as_of=as_of,
                is_alive=False,
                is_degraded=False,
                degraded_since=None,
                last_tick_at=None,
                equity_points_count=0,
                latest_equity_after_tax=None,
            ),
        )


def _resp(aid: str, *, equity: str, dd: str, alive: bool = True) -> PaperStateResponse:
    return PaperStateResponse(
        account_id=AccountId(aid),
        as_of=datetime(2026, 5, 23, 12, 0, tzinfo=UTC),
        is_alive=alive,
        is_degraded=False,
        degraded_since=None,
        last_tick_at=None,
        equity_points_count=10,
        latest_equity_after_tax=Decimal(equity),
        drawdown_pct=Decimal(dd),
        instrument_symbol="ASML",
    )


# ---------------------------------------------------------------------------
# household_snapshot unit tests
# ---------------------------------------------------------------------------


def test_household_snapshot_empty_registry_returns_zero_counts() -> None:
    snap = household_snapshot(
        _FakeRegistry(), _FakeReader(), as_of=datetime.now(tz=UTC)
    )
    assert snap.account_count == 0
    assert snap.total_equity_after_tax is None
    assert snap.max_drawdown_pct is None
    assert snap.accounts == ()


def test_household_snapshot_sums_equity_and_picks_worst_drawdown() -> None:
    ids = (
        AccountId("paper-a"),
        AccountId("paper-b"),
        AccountId("paper-c"),
    )
    responses = {
        ids[0]: _resp("paper-a", equity="10000", dd="2.5"),
        ids[1]: _resp("paper-b", equity="20000", dd="8.0"),
        ids[2]: _resp("paper-c", equity="15000", dd="3.0"),
    }
    snap = household_snapshot(
        _FakeRegistry(ids=ids),
        _FakeReader(responses=responses),
        as_of=datetime.now(tz=UTC),
    )
    assert snap.account_count == 3
    assert snap.total_equity_after_tax == Decimal("45000")
    # Worst drawdown across the three.
    assert snap.max_drawdown_pct == Decimal("8.0")
    # Per-account rows in registry order.
    assert tuple(a.account_id for a in snap.accounts) == (
        "paper-a",
        "paper-b",
        "paper-c",
    )


def test_household_snapshot_skips_accounts_with_no_equity() -> None:
    ids = (AccountId("paper-empty"),)
    snap = household_snapshot(
        _FakeRegistry(ids=ids),
        _FakeReader(),  # default response has no equity
        as_of=datetime.now(tz=UTC),
    )
    assert snap.account_count == 1
    assert snap.total_equity_after_tax is None
    assert snap.max_drawdown_pct is None


# ---------------------------------------------------------------------------
# Dashboard rendering tests
# ---------------------------------------------------------------------------


def _client_with_registry(
    *, account_ids: tuple[str, ...], responses: dict[str, PaperStateResponse] | None = None
):
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    ids = tuple(AccountId(a) for a in account_ids)
    registry = _FakeRegistry(ids=ids)
    reader = _FakeReader(
        responses={
            AccountId(k): v for k, v in (responses or {}).items()
        }
    )
    state = WebappState(
        token_verifier=verifier,
        runtime_registry=registry,
        paper_state_reader=reader,
    )
    return TestClient(create_app(state)), verifier


def _token(verifier):
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC))


def test_single_account_dashboard_does_not_render_household_panel() -> None:
    """REQ_NF_ACC_001 spirit — single-account view stays
    bit-identical to the no-multi-account variant. The household
    panel SHALL NOT render."""
    client, verifier = _client_with_registry(account_ids=("paper-a",))
    body = client.get(
        "/?account_id=paper-a",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    ).text
    # The switcher card itself renders (since 1 session counts);
    # the household sub-section SHALL NOT.
    assert "Household roll-up" not in body
    assert "Per-account" not in body


def test_multi_account_dashboard_renders_household_roll_up() -> None:
    """REQ_F_WEB2_008 — 2+ live accounts SHALL surface the
    household-drawdown indicator + per-account table."""
    responses = {
        "paper-a": _resp("paper-a", equity="10000", dd="2.5"),
        "paper-b": _resp("paper-b", equity="20000", dd="12.0"),
    }
    client, verifier = _client_with_registry(
        account_ids=("paper-a", "paper-b"),
        responses=responses,
    )
    body = client.get(
        "/?account_id=paper-a",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    ).text
    assert "Household roll-up" in body
    # Total equity = 30000.
    assert "30000.00" in body
    # Worst-of-N drawdown = 12.00.
    assert "12.00" in body
    # Tone badge SHALL pick the "warn" class because 12% >= 5%.
    assert (
        'aria-label="Household worst drawdown 12.0 percent"' in body
        or 'aria-label="Household worst drawdown 12 percent"' in body
    )
    # Per-account table renders both ids as switch links.
    assert "/?account_id=paper-a" in body
    assert "/?account_id=paper-b" in body
    # Both rows carry an aria-labelled state badge.
    assert 'aria-label="Account state Live"' in body


def test_household_drawdown_above_15_uses_error_tone() -> None:
    responses = {
        "paper-a": _resp("paper-a", equity="10000", dd="20.0"),
        "paper-b": _resp("paper-b", equity="20000", dd="3.0"),
    }
    client, verifier = _client_with_registry(
        account_ids=("paper-a", "paper-b"),
        responses=responses,
    )
    body = client.get(
        "/?account_id=paper-a",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    ).text
    # The worst-drawdown badge SHALL carry the error tone.
    match = re.search(
        r'<span class="badge (?P<tone>\w+)"[^>]*aria-label="Household worst drawdown 20',
        body,
    )
    assert match is not None
    assert match.group("tone") == "error"


def test_household_drawdown_low_uses_success_tone() -> None:
    responses = {
        "paper-a": _resp("paper-a", equity="10000", dd="0.5"),
        "paper-b": _resp("paper-b", equity="20000", dd="1.0"),
    }
    client, verifier = _client_with_registry(
        account_ids=("paper-a", "paper-b"),
        responses=responses,
    )
    body = client.get(
        "/?account_id=paper-a",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    ).text
    match = re.search(
        r'<span class="badge (?P<tone>\w+)"[^>]*aria-label="Household worst drawdown 1\.',
        body,
    )
    assert match is not None
    assert match.group("tone") == "success"
