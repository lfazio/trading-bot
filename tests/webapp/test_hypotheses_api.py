"""CR-027 / TC_QNT_OPS_002..006 — operator hypothesis-filing API.

Three JSON endpoints:
- POST /api/hypotheses
- GET  /api/hypotheses
- GET  /api/strategies/{strategy_id}/hypotheses

All routes are per-account-token gated. Household claim REJECTED.

REQ refs:
- REQ_F_QNT_007..010 (operator surface).
- REQ_SDD_QNT_010..012 (route shapes).
- REQ_F_QNT_004 (5-gate Validator inline).
- REQ_F_TOK_001..005 / REQ_F_ACC_010 / REQ_NF_TOK_001 (auth + audit).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.result import Err, Ok, Result
from trading_system.strategy_lab.quant.hypothesis import (
    DatasetWindow,
    Direction,
    Hypothesis,
    HypothesisId,
    HypothesisState,
)
from trading_system.strategy_lab.quant.validator import (
    HypothesisValidator,
    ValidatorConfig,
)
from trading_system.strategy_lab.quant.webapp_adapter import (
    StrategyLabHypothesisFiler,
    StrategyLabHypothesisLister,
)
from trading_system.webapp import WebappState, create_app


_SECRET = b"hypothesis-route-secret" * 4
_ACCOUNT = "alpha"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeFiler:
    """In-memory ``HypothesisFilerView`` — builds + validates +
    persists. Test double matches the production
    ``StrategyLabHypothesisFiler`` interface but works without
    SQLite — the rows are kept in a Python list."""

    validator: HypothesisValidator
    rows: list[Hypothesis] = field(default_factory=list)
    _seq: int = 0

    def file(
        self, *, payload: dict, account_id: AccountId
    ) -> Result[dict, str]:
        del account_id  # one-account tests
        now = datetime.now(tz=UTC)
        self._seq += 1
        hid = HypothesisId(f"hyp-{now.isoformat()}-{self._seq:04d}")
        try:
            direction = Direction(payload["expected_direction"])
        except (KeyError, ValueError):
            return Err("hypothesis:bad_expected_direction")
        try:
            window = DatasetWindow(
                start=_parse_iso(payload["dataset_window"]["start"]),
                end=_parse_iso(payload["dataset_window"]["end"]),
                frequency=str(payload["dataset_window"]["frequency"]),
            )
        except Exception as e:  # noqa: BLE001
            return Err(f"hypothesis:structural:dataset_window:{e!s}")
        try:
            h = Hypothesis(
                id=hid,
                claim=str(payload.get("claim", "")),
                falsification_criterion=str(
                    payload.get("falsification_criterion", "")
                ),
                dataset_window=window,
                metric=str(payload.get("metric", "")),
                expected_direction=direction,
                operator_rationale=str(payload.get("operator_rationale", "")),
                created_at=now,
                state=HypothesisState.PENDING,
            )
        except ValueError as e:
            return Err(f"hypothesis:structural:{e!s}")
        v = self.validator.validate(h)
        if isinstance(v, Err):
            return Err(v.error)
        self.rows.append(h)
        return Ok({"id": str(h.id), "state": "PENDING", "validated": False})


@dataclass
class _FakeLister:
    """In-memory ``HypothesisListerView``."""

    filer: _FakeFiler

    def list_filed(
        self, *, account_id: AccountId
    ) -> Result[tuple[dict, ...], str]:
        del account_id
        rows = tuple(_hypothesis_to_dict(h) for h in self.filer.rows)
        return Ok(rows)


def _parse_iso(raw):
    if isinstance(raw, datetime):
        return raw
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _hypothesis_to_dict(h: Hypothesis) -> dict:
    return {
        "id": str(h.id),
        "claim": h.claim,
        "metric": h.metric,
        "state": h.state.value,
        "created_at": h.created_at.isoformat(),
    }


@dataclass
class _StubImprovementReport:
    hypothesis_ids: tuple[str, ...]


@dataclass
class _FakeLookup:
    by_strategy: dict[str, _StubImprovementReport] = field(default_factory=dict)

    def latest_for(self, strategy_id: StrategyId):
        report = self.by_strategy.get(str(strategy_id))
        if report is None:
            # Documented "no lineage" signal — empty tuple, not Err.
            return None
        return report


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _verifier() -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)


def _household_token(v: AccountScopedTokenVerifier) -> str:
    return v.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))


def _account_token(v: AccountScopedTokenVerifier, aid: str = _ACCOUNT) -> str:
    return v.issue(account_id=aid, now=datetime.now(UTC))


def _validator() -> HypothesisValidator:
    return HypothesisValidator(cfg=ValidatorConfig())


def _make_app(
    *,
    filer: _FakeFiler | None = None,
    lookup: _FakeLookup | None = None,
):
    if filer is None:
        filer = _FakeFiler(validator=_validator())
    state = WebappState(
        token_verifier=_verifier(),
        hypothesis_filer=filer,
        hypothesis_lister=_FakeLister(filer=filer),
        improvement_report_lookup=lookup,
    )
    return create_app(state)


def _valid_body() -> dict[str, Any]:
    """A body the 5-gate Validator accepts."""
    window_start = datetime(2024, 1, 1, tzinfo=UTC).isoformat()
    window_end = datetime(2024, 12, 31, tzinfo=UTC).isoformat()
    return {
        "claim": (
            "Stocks with high dividend yield outperform on "
            "adjusted_sharpe vs the index"
        ),
        "falsification_criterion": (
            "If adjusted_sharpe falls below baseline by more than 0.2 "
            "over a long OOS window, the hypothesis is rejected"
        ),
        "dataset_window": {
            "start": window_start,
            "end": window_end,
            "frequency": "1d",
        },
        "metric": "adjusted_sharpe",
        "expected_direction": "positive",
        "operator_rationale": (
            "Dividend yield as quality proxy — historically validated in "
            "Phase-5 cohort backtests"
        ),
        "account_id": _ACCOUNT,
    }


# ---------------------------------------------------------------------------
# TC_QNT_OPS_002 — POST happy path
# ---------------------------------------------------------------------------


def test_post_hypothesis_happy_path_returns_201_and_persists() -> None:
    """REQ_F_QNT_008 / REQ_SDD_QNT_010 — operator-token-gated
    happy path; 201 + canonical-JSON body; row persisted."""
    filer = _FakeFiler(validator=_validator())
    app = _make_app(filer=filer)
    client = TestClient(app)
    token = _account_token(_verifier(), _ACCOUNT)
    r = client.post(
        "/api/hypotheses",
        json=_valid_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    body = json.loads(r.content)
    assert body["state"] == "PENDING"
    assert body["validated"] is False
    assert body["id"].startswith("hyp-")
    # Row landed in the repo.
    assert len(filer.rows) == 1
    assert filer.rows[0].id == body["id"]


# ---------------------------------------------------------------------------
# TC_QNT_OPS_003 — Validator-rejection path
# ---------------------------------------------------------------------------


def test_post_hypothesis_empty_claim_returns_400_structural() -> None:
    """REQ_F_QNT_008 — Pydantic catches empty string FIRST. The
    response is a 422 Unprocessable Entity (FastAPI default for
    body validation failures)."""
    filer = _FakeFiler(validator=_validator())
    app = _make_app(filer=filer)
    client = TestClient(app)
    token = _account_token(_verifier(), _ACCOUNT)
    body = _valid_body()
    body["claim"] = ""
    r = client.post(
        "/api/hypotheses",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    # Pydantic structural validation is the first gate — 422 is
    # FastAPI's documented response. No row persisted.
    assert r.status_code in (400, 422)
    assert len(filer.rows) == 0


def test_post_hypothesis_unknown_metric_returns_400_metric_mismatch() -> None:
    """REQ_F_QNT_004 — gate 4 rejects unknown metrics."""
    filer = _FakeFiler(validator=_validator())
    app = _make_app(filer=filer)
    client = TestClient(app)
    token = _account_token(_verifier(), _ACCOUNT)
    body = _valid_body()
    body["metric"] = "not_a_real_metric"
    r = client.post(
        "/api/hypotheses",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    err_body = json.loads(r.content)
    assert "hypothesis:metric_mismatch" in (
        err_body.get("error", "") + err_body.get("detail", "")
    )
    assert len(filer.rows) == 0


# ---------------------------------------------------------------------------
# TC_QNT_OPS_004 — Authorisation: household + cross-account + missing
# ---------------------------------------------------------------------------


def test_post_hypothesis_household_claim_rejected_403() -> None:
    """REQ_F_QNT_008 / REQ_SDD_QNT_010 — household-claim REJECTED."""
    app = _make_app()
    client = TestClient(app)
    token = _household_token(_verifier())
    r = client.post(
        "/api/hypotheses",
        json=_valid_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    body = json.loads(r.content)
    assert body.get("error", "") == "registry:household_claim_rejected"


def test_post_hypothesis_cross_account_token_rejected_401() -> None:
    """A token claiming a different account_id SHALL be rejected."""
    app = _make_app()
    client = TestClient(app)
    # Token for "other" account; body targets _ACCOUNT.
    token = _account_token(_verifier(), "other")
    r = client.post(
        "/api/hypotheses",
        json=_valid_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_post_hypothesis_missing_authorization_returns_401() -> None:
    app = _make_app()
    client = TestClient(app)
    r = client.post("/api/hypotheses", json=_valid_body())
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# TC_QNT_OPS_005 — GET paginated read
# ---------------------------------------------------------------------------


def test_get_hypotheses_returns_canonical_json_byte_identical() -> None:
    """REQ_F_QNT_009 / REQ_SDD_QNT_011 — two reads against the same
    repository state SHALL be byte-identical."""
    filer = _FakeFiler(validator=_validator())
    # Seed a few hypotheses.
    for i in range(3):
        filer.rows.append(_build_hypothesis(suffix=str(i)))
    app = _make_app(filer=filer)
    client = TestClient(app)
    token = _account_token(_verifier(), _ACCOUNT)
    r1 = client.get(
        f"/api/hypotheses?account_id={_ACCOUNT}",
        headers={"Authorization": f"Bearer {token}"},
    )
    r2 = client.get(
        f"/api/hypotheses?account_id={_ACCOUNT}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.content == r2.content
    body = json.loads(r1.content)
    assert body["page"] == 1
    assert body["per_page"] == 25
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_get_hypotheses_rejects_per_page_above_cap() -> None:
    """REQ_F_QNT_009 — `per_page` capped at 100."""
    app = _make_app()
    client = TestClient(app)
    token = _account_token(_verifier(), _ACCOUNT)
    r = client.get(
        f"/api/hypotheses?account_id={_ACCOUNT}&per_page=200",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    body = json.loads(r.content)
    assert "pagination_per_page_exceeds_max" in body.get("error", "")


# ---------------------------------------------------------------------------
# TC_QNT_OPS_001 — GET /strategies/hypotheses view route
# ---------------------------------------------------------------------------


def test_get_hypotheses_view_renders_three_sections() -> None:
    """REQ_F_QNT_007 / REQ_SDD_QNT_009 — view renders form + two
    tables. Cookie auth via the household token (the view router
    accepts any valid claim per ``verify_any_valid_claim``)."""
    filer = _FakeFiler(validator=_validator())
    filer.rows.append(_build_hypothesis(suffix="1"))
    app = _make_app(filer=filer)
    client = TestClient(app)
    token = _household_token(_verifier())
    # The view router consumes the bearer token like the JSON API.
    r = client.get(
        "/strategies/hypotheses",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Three sections present.
    assert 'id="file-hypothesis"' in body
    assert 'id="pending-rejected"' in body
    assert 'id="validated"' in body
    # The form targets /api/hypotheses via hx-post.
    assert 'hx-post="/api/hypotheses"' in body


def test_get_hypotheses_view_redirects_when_unauthenticated() -> None:
    """REQ_F_QNT_007 — view route requires a valid token claim."""
    app = _make_app()
    client = TestClient(app)
    r = client.get("/strategies/hypotheses", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


# ---------------------------------------------------------------------------
# TC_QNT_OPS_006 — Per-strategy lineage
# ---------------------------------------------------------------------------


def test_get_strategy_hypotheses_returns_lineage() -> None:
    """REQ_F_QNT_010 / REQ_SDD_QNT_012 — returns
    ImprovementReport.hypothesis_ids tuple."""
    lookup = _FakeLookup(
        by_strategy={
            "strat-1": _StubImprovementReport(
                hypothesis_ids=("hyp-a", "hyp-b")
            )
        }
    )
    app = _make_app(lookup=lookup)
    client = TestClient(app)
    r = client.get("/api/strategies/strat-1/hypotheses")
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["strategy_id"] == "strat-1"
    assert body["hypothesis_ids"] == ["hyp-a", "hyp-b"]


def test_get_strategy_hypotheses_unknown_strategy_returns_empty_tuple() -> None:
    """REQ_F_QNT_010 — unknown strategy_id (or no ImprovementReport)
    returns 200 + empty list, the documented "no lineage" signal."""
    lookup = _FakeLookup()
    app = _make_app(lookup=lookup)
    client = TestClient(app)
    r = client.get("/api/strategies/handcrafted-2024/hypotheses")
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["hypothesis_ids"] == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_hypothesis(*, suffix: str) -> Hypothesis:
    now = datetime.now(tz=UTC)
    return Hypothesis(
        id=HypothesisId(f"hyp-{suffix}-{now.isoformat()}"),
        claim=(
            "Stocks with high dividend yield outperform on "
            "adjusted_sharpe versus the index"
        ),
        falsification_criterion=(
            "If adjusted_sharpe falls more than 0.2 below baseline "
            "over a 6-month OOS window, the hypothesis is rejected"
        ),
        dataset_window=DatasetWindow(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
            frequency="1d",
        ),
        metric="adjusted_sharpe",
        expected_direction=Direction.POSITIVE,
        operator_rationale="seed-row for paginated-read test",
        created_at=now - timedelta(seconds=int(suffix) if suffix.isdigit() else 0),
        state=HypothesisState.PENDING,
    )
