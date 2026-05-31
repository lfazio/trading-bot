"""Tests for the four Phase-B read routes — REQ_F_WEB_002 (b/c/d/e).

Each route follows the same shape (Reader Protocol + handler
factory + path parser + canonical response). The tests verify:

- Happy path: household-claim token + correct path ⇒ 200 +
  canonical JSON body shaped from the response schema.
- Method reject: any verb other than GET ⇒ 405.
- Path reject: malformed path ⇒ 400.
- Auth reject: per-account token ⇒ 401
  (``registry:token_invalid``); household-claim is required
  per REQ_F_WEB_005's read-endpoint contract.
- REQ_NF_WEB_002 byte-identical replay: two calls with the
  same Request body produce the same canonical body.

The reader Protocol is satisfied with a tiny stub so the route
stays decoupled from concrete persistence types.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.webui.auth import WebAuth
from trading_system.webui.routes.backtests_archive import (
    build_backtests_archive_handler,
)
from trading_system.webui.routes.improvement_reports_history import (
    build_improvement_reports_history_handler,
)
from trading_system.webui.routes.registry_list import (
    build_registry_list_handler,
)
from trading_system.webui.routes.summary import (
    build_summary_handler,
)
from trading_system.webui.schemas import (
    BacktestArchiveLine,
    BacktestsArchiveResponse,
    ImprovementReportLine,
    ImprovementReportsHistoryResponse,
    RegistryEntryLine,
    RegistryListResponse,
    SummaryResponse,
)
from trading_system.webui.server import Request


_NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)


def _auth() -> tuple[WebAuth, AccountScopedTokenVerifier]:
    v = AccountScopedTokenVerifier(
        secret=b"shh-test-secret-for-phase-b", ttl_seconds=300, _clock=lambda: _NOW
    )
    return WebAuth(verifier=v), v


# ---------------------------------------------------------------------------
# /accounts/<aid>/summary
# ---------------------------------------------------------------------------


def _summary() -> SummaryResponse:
    return SummaryResponse(
        account_id=AccountId("alpha"),
        as_of=_NOW,
        equity_after_tax=Decimal("12345.67"),
        realized_pnl=Decimal("1000.00"),
        unrealized_pnl=Decimal("2000.00"),
        dividend_income_ytd=Decimal("345.67"),
        max_drawdown_pct=Decimal("0.08"),
    )


@dataclass(slots=True)
class _SummaryStub:
    payload: SummaryResponse

    def summary(self, *, account_id: AccountId, as_of: datetime) -> SummaryResponse:
        return self.payload


def test_summary_happy_path() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_summary_handler(
        auth=auth, reader=_SummaryStub(payload=_summary())
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/summary",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 200
    obj = json.loads(resp.body)
    assert obj["account_id"] == "alpha"
    assert obj["equity_after_tax"] == "12345.67"
    assert obj["max_drawdown_pct"] == "0.08"


def test_summary_rejects_per_account_token() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    handler = build_summary_handler(
        auth=auth, reader=_SummaryStub(payload=_summary())
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/summary",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 401
    assert json.loads(resp.body)["error"] == "registry:token_invalid"


def test_summary_rejects_wrong_method() -> None:
    auth, _v = _auth()
    handler = build_summary_handler(
        auth=auth, reader=_SummaryStub(payload=_summary())
    )
    resp = handler(
        Request(method="POST", path="/accounts/alpha/summary", headers={})
    )
    assert resp.status_code == 405


def test_summary_rejects_malformed_path() -> None:
    auth, _v = _auth()
    handler = build_summary_handler(
        auth=auth, reader=_SummaryStub(payload=_summary())
    )
    resp = handler(Request(method="GET", path="/bogus", headers={}))
    assert resp.status_code == 400


def test_summary_byte_identical_replay() -> None:
    """REQ_NF_WEB_002 — identical inputs ⇒ identical bytes."""
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_summary_handler(
        auth=auth, reader=_SummaryStub(payload=_summary())
    )
    req = Request(
        method="GET",
        path="/accounts/alpha/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    a = handler(req)
    b = handler(req)
    assert a.body == b.body


# ---------------------------------------------------------------------------
# /accounts/<aid>/registry
# ---------------------------------------------------------------------------


def _registry_list() -> RegistryListResponse:
    return RegistryListResponse(
        account_id=AccountId("alpha"),
        as_of=_NOW,
        entries=(
            RegistryEntryLine(
                strategy_id=StrategyId("core-v1"),
                git_sha="abc123",
                config_hash="hash-1",
                validated=True,
                promoted_at=_NOW,
            ),
            RegistryEntryLine(
                strategy_id=StrategyId("ensemble-v2"),
                git_sha="def456",
                config_hash="hash-2",
                validated=False,
                promoted_at=_NOW,
            ),
        ),
    )


@dataclass(slots=True)
class _RegistryListStub:
    payload: RegistryListResponse

    def registry_list(
        self, *, account_id: AccountId, as_of: datetime
    ) -> RegistryListResponse:
        return self.payload


def test_registry_list_happy_path() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_registry_list_handler(
        auth=auth, reader=_RegistryListStub(payload=_registry_list())
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/registry",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 200
    obj = json.loads(resp.body)
    assert obj["account_id"] == "alpha"
    assert len(obj["entries"]) == 2
    assert obj["entries"][0]["strategy_id"] == "core-v1"
    assert obj["entries"][0]["validated"] is True


def test_registry_list_rejects_per_account_token() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    handler = build_registry_list_handler(
        auth=auth, reader=_RegistryListStub(payload=_registry_list())
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/registry",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 401


def test_registry_list_byte_identical_replay() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_registry_list_handler(
        auth=auth, reader=_RegistryListStub(payload=_registry_list())
    )
    req = Request(
        method="GET",
        path="/accounts/alpha/registry",
        headers={"Authorization": f"Bearer {token}"},
    )
    a = handler(req)
    b = handler(req)
    assert a.body == b.body


# ---------------------------------------------------------------------------
# /accounts/<aid>/backtests
# ---------------------------------------------------------------------------


def _backtests_archive(per_page: int = 25, page: int = 1) -> BacktestsArchiveResponse:
    return BacktestsArchiveResponse(
        account_id=AccountId("alpha"),
        as_of=_NOW,
        entries=(
            BacktestArchiveLine(
                strategy_id=StrategyId("core-v1"),
                git_sha="abc123",
                config_hash="hash-1",
                seed=42,
                final_equity_after_tax=Decimal("11000.00"),
                max_drawdown_pct=Decimal("0.10"),
                sharpe=Decimal("1.25"),
                completed_at=_NOW,
            ),
        ),
        per_page=per_page,
        page=page,
    )


@dataclass(slots=True)
class _BacktestsArchiveStub:
    captured: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.captured = []

    def backtests_archive(
        self,
        *,
        account_id: AccountId,
        as_of: datetime,
        per_page: int,
        page: int,
    ) -> BacktestsArchiveResponse:
        self.captured.append((per_page, page))
        return _backtests_archive(per_page=per_page, page=page)


def test_backtests_archive_happy_path_default_pagination() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    stub = _BacktestsArchiveStub()
    handler = build_backtests_archive_handler(auth=auth, reader=stub)
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/backtests",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 200
    # Reader called with the documented defaults.
    assert stub.captured == [(25, 1)]
    obj = json.loads(resp.body)
    assert obj["per_page"] == 25
    assert obj["page"] == 1


def test_backtests_archive_clamps_pagination_to_query_params() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    stub = _BacktestsArchiveStub()
    handler = build_backtests_archive_handler(auth=auth, reader=stub)
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/backtests?per_page=10&page=3",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 200
    assert stub.captured == [(10, 3)]


def test_backtests_archive_rejects_per_page_out_of_bounds() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_backtests_archive_handler(
        auth=auth, reader=_BacktestsArchiveStub()
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/backtests?per_page=999",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 400
    assert "per_page_out_of_bounds" in json.loads(resp.body)["error"]


def test_backtests_archive_rejects_negative_page() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_backtests_archive_handler(
        auth=auth, reader=_BacktestsArchiveStub()
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/backtests?page=0",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 400
    assert "page_out_of_bounds" in json.loads(resp.body)["error"]


def test_backtests_archive_byte_identical_replay() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_backtests_archive_handler(
        auth=auth, reader=_BacktestsArchiveStub()
    )
    req = Request(
        method="GET",
        path="/accounts/alpha/backtests",
        headers={"Authorization": f"Bearer {token}"},
    )
    a = handler(req)
    b = handler(req)
    assert a.body == b.body


# ---------------------------------------------------------------------------
# /accounts/<aid>/improvement-reports
# ---------------------------------------------------------------------------


def _improvement_reports() -> ImprovementReportsHistoryResponse:
    return ImprovementReportsHistoryResponse(
        account_id=AccountId("alpha"),
        as_of=_NOW,
        reports=(
            ImprovementReportLine(
                cycle_id="cycle-001",
                created_at=_NOW,
                git_sha="abc123",
                accepted_count=2,
                rejected_count=3,
            ),
        ),
    )


@dataclass(slots=True)
class _ImprovementReportsStub:
    payload: ImprovementReportsHistoryResponse

    def improvement_reports_history(
        self, *, account_id: AccountId, as_of: datetime
    ) -> ImprovementReportsHistoryResponse:
        return self.payload


def test_improvement_reports_history_happy_path() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_improvement_reports_history_handler(
        auth=auth, reader=_ImprovementReportsStub(payload=_improvement_reports())
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/improvement-reports",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 200
    obj = json.loads(resp.body)
    assert obj["account_id"] == "alpha"
    assert len(obj["reports"]) == 1
    assert obj["reports"][0]["cycle_id"] == "cycle-001"
    assert obj["reports"][0]["accepted_count"] == 2


def test_improvement_reports_history_rejects_per_account_token() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    handler = build_improvement_reports_history_handler(
        auth=auth, reader=_ImprovementReportsStub(payload=_improvement_reports())
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/improvement-reports",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 401


def test_improvement_reports_history_byte_identical_replay() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_improvement_reports_history_handler(
        auth=auth, reader=_ImprovementReportsStub(payload=_improvement_reports())
    )
    req = Request(
        method="GET",
        path="/accounts/alpha/improvement-reports",
        headers={"Authorization": f"Bearer {token}"},
    )
    a = handler(req)
    b = handler(req)
    assert a.body == b.body
