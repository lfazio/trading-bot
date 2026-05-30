"""CR-027 — Operator hypothesis-filing FastAPI routes.

Three JSON endpoints:

  POST /api/hypotheses                            — file a new hypothesis
  GET  /api/hypotheses                            — paginated read
  GET  /api/strategies/{strategy_id}/hypotheses   — per-strategy lineage

All routes are gated by the per-account-scoped operator token.
The household claim is REJECTED — hypotheses are per-account
(REQ_F_PER_009 / REQ_F_QNT_008).

REQ refs:
- REQ_F_QNT_007..010 — operator surface.
- REQ_SDD_QNT_009..012 — route shapes.
- REQ_F_TOK_001..005 / REQ_F_ACC_010 / REQ_NF_TOK_001 —
  operator-token gating + SECURITY audit.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.responses import Response

from trading_system.accounts.token_verifier import HOUSEHOLD_CLAIM
from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.observability import structured_log
from trading_system.result import Err
from trading_system.webapp.canonical import (
    canonical_error_response,
    canonical_json_response,
)


_AUDIT_LOGGER = logging.getLogger(__name__)


router = APIRouter()


# ---------------------------------------------------------------------------
# Protocol slots — small surfaces the operator wiring fills in
# ---------------------------------------------------------------------------


@runtime_checkable
class HypothesisFilerView(Protocol):
    """REQ_SDD_QNT_010 — opaque filer slot wired at boot.

    The route SHALL NOT import the concrete ``Hypothesis``
    dataclass (REQ_NF_QNT_001 — `strategy_lab/quant/` is offline
    research; webapp imports stay structurally clean per
    REQ_SDD_FAS_001). The filer accepts the validated Pydantic
    payload as a plain dict + the account_id; the implementation
    (wired at boot in operator code) handles construction,
    runs the 5-gate Validator, and persists on Ok.

    Returns ``Result[dict, str]`` — Ok carries
    ``{"id", "state", "validated"}``; Err carries the categorised
    ``hypothesis:*`` Err string from the Validator (REQ_F_QNT_004
    closed Err set)."""

    def file(
        self, *, payload: dict[str, Any], account_id: AccountId
    ) -> Any: ...


@runtime_checkable
class HypothesisListerView(Protocol):
    """REQ_SDD_QNT_011 — paginated read slot.

    Returns ``Result[tuple[dict, ...], str]``; each dict matches the
    documented canonical-JSON Hypothesis shape so two reads against
    the same store are byte-identical (REQ_NF_WEB_002)."""

    def list_filed(self, *, account_id: AccountId) -> Any: ...


@runtime_checkable
class ImprovementReportLookup(Protocol):
    """REQ_SDD_QNT_012 — per-strategy lineage lookup.

    The concrete implementation queries the CR-008 archive for the
    most recent ImprovementReport that promoted ``strategy_id``;
    the route returns its ``hypothesis_ids`` tuple."""

    def latest_for(
        self, strategy_id: StrategyId
    ) -> object: ...  # Option[ImprovementReport]


# ---------------------------------------------------------------------------
# Pydantic request shape — REQ_SDD_QNT_010 step (b)
# ---------------------------------------------------------------------------


class _DatasetWindowBody(BaseModel):
    start: datetime
    end: datetime
    frequency: str = Field(min_length=1)


class HypothesisCreateBody(BaseModel):
    """Wire shape — operator-supplied; server fills ``id`` /
    ``state`` / ``created_at`` (REQ_SDD_QNT_010 step c)."""

    claim: str = Field(min_length=1)
    falsification_criterion: str = Field(min_length=1)
    dataset_window: _DatasetWindowBody
    metric: str = Field(min_length=1)
    expected_direction: str  # "positive" / "negative" / "two_tailed"
    operator_rationale: str = Field(min_length=1)
    account_id: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Auth helpers (mirrors api/live_mode.py)
# ---------------------------------------------------------------------------


_BEARER_PREFIX = "Bearer "


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX) :].strip() or None
    if auth:
        return auth.strip() or None
    legacy = request.headers.get("x-operator-token", "").strip()
    return legacy or None


def _verifier(request: Request):
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:token_verifier_missing",
        )
    return verifier


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_account_token(
    request: Request, *, account_id: AccountId
) -> tuple[str, str] | Response:
    """REQ_F_QNT_008 / REQ_SDD_QNT_010 — household-claim REJECTED;
    cross-account REJECTED; missing Authorization ⇒ 401."""
    bearer = _extract_bearer(request)
    if bearer is None:
        return canonical_error_response(
            "registry:token_invalid",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    verifier = _verifier(request)
    if verifier.verify(bearer, account_id=HOUSEHOLD_CLAIM):
        return canonical_error_response(
            "registry:household_claim_rejected",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not verifier.verify(bearer, account_id=str(account_id)):
        return canonical_error_response(
            "registry:token_invalid",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return bearer, _token_hash(bearer)


def _audit(
    *,
    event: str,
    account_id: AccountId,
    outcome: str,
    token_hash: str,
    hypothesis_id: str = "",
    message: str = "",
) -> None:
    """REQ_NF_TOK_001 — every authorised hypothesis action emits a
    SECURITY structured-log entry. The raw token SHALL NOT appear
    in the payload — only ``token_hash``."""
    level = logging.INFO if outcome == "ok" else logging.WARNING
    payload: dict[str, object] = {
        "event": event,
        "account_id": str(account_id),
        "outcome": outcome,
        "token_hash": token_hash,
    }
    if hypothesis_id:
        payload["hypothesis_id"] = hypothesis_id
    if message:
        payload["message"] = message
    structured_log(
        _AUDIT_LOGGER, level, "security", f"hypothesis:{event}", **payload
    )


def _filer(request: Request) -> HypothesisFilerView | None:
    return getattr(request.app.state, "hypothesis_filer", None)


def _lister(request: Request) -> HypothesisListerView | None:
    return getattr(request.app.state, "hypothesis_lister", None)


def _improvement_report_lookup(
    request: Request,
) -> ImprovementReportLookup | None:
    return getattr(request.app.state, "improvement_report_lookup", None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/api/hypotheses",
    response_class=Response,
    summary="File a new hypothesis (CR-027 / REQ_F_QNT_008)",
)
def post_hypothesis(
    body: HypothesisCreateBody, request: Request
) -> Response:
    """REQ_F_QNT_008 / REQ_SDD_QNT_010 — operator-token-gated
    submission; 5-gate Validator inline; 201 on Ok, 400 on
    categorised hypothesis:* Err."""
    account_id = AccountId(body.account_id)
    auth = _check_account_token(request, account_id=account_id)
    if isinstance(auth, Response):
        return auth
    _token, token_hash = auth

    filer = _filer(request)
    if filer is None:
        return canonical_error_response(
            "webapp:hypothesis_layer_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Delegate construction + validation + persistence to the wired
    # filer (REQ_SDD_QNT_010 — strategy_lab/quant/ stays offline-only
    # per REQ_NF_QNT_001; the route consumes via Protocol slot).
    payload = body.model_dump(mode="json")
    file_result = filer.file(payload=payload, account_id=account_id)

    if isinstance(file_result, Err):
        _audit(
            event="filed",
            account_id=account_id,
            outcome="rejected",
            token_hash=token_hash,
            message=file_result.error,
        )
        # `hypothesis:*` Errs map to 400; `persistence:*` map to 500.
        status_code = (
            status.HTTP_400_BAD_REQUEST
            if file_result.error.startswith("hypothesis:")
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        return canonical_error_response(
            file_result.error, status_code=status_code
        )

    record = file_result.value
    hypothesis_id = str(record.get("id", ""))
    _audit(
        event="filed",
        account_id=account_id,
        outcome="ok",
        token_hash=token_hash,
        hypothesis_id=hypothesis_id,
    )
    return canonical_json_response(
        {
            "id": hypothesis_id,
            "state": record.get("state", "PENDING"),
            "validated": bool(record.get("validated", False)),
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.get(
    "/api/hypotheses",
    response_class=Response,
    summary="List filed hypotheses (CR-027 / REQ_F_QNT_009)",
)
def get_hypotheses(
    request: Request,
    account_id: str,
    page: int = 1,
    per_page: int = 25,
) -> Response:
    """REQ_F_QNT_009 / REQ_SDD_QNT_011 — paginated per-account read.
    Canonical-JSON response preserves REQ_NF_WEB_002 byte-determinism
    across two calls against identical repository state."""
    if per_page < 1 or per_page > 100:
        return canonical_error_response(
            f"webapp:pagination_per_page_exceeds_max:{per_page}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if page < 1:
        return canonical_error_response(
            f"webapp:pagination_page_invalid:{page}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    aid = AccountId(account_id)
    auth = _check_account_token(request, account_id=aid)
    if isinstance(auth, Response):
        return auth

    lister = _lister(request)
    if lister is None:
        return canonical_error_response(
            "webapp:hypothesis_layer_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    all_result = lister.list_filed(account_id=aid)
    if isinstance(all_result, Err):
        return canonical_error_response(
            all_result.error,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    rows = list(all_result.value)
    total = len(rows)
    # Most-recently created first. Rows are plain dicts —
    # ``created_at`` is an ISO-8601 string from the filer side.
    rows.sort(key=lambda h: h.get("created_at", ""), reverse=True)
    start = (page - 1) * per_page
    items = rows[start : start + per_page]

    return canonical_json_response(
        {
            "page": page,
            "per_page": per_page,
            "total": total,
            "items": items,
        }
    )


@router.get(
    "/api/strategies/{strategy_id}/hypotheses",
    response_class=Response,
    summary="Per-strategy hypothesis lineage (CR-027 / REQ_F_QNT_010)",
)
def get_strategy_hypotheses(strategy_id: str, request: Request) -> Response:
    """REQ_F_QNT_010 / REQ_SDD_QNT_012 — return the strategy's
    ``ImprovementReport.hypothesis_ids`` tuple. Hand-curated
    strategies (no ImprovementReport) ⇒ empty tuple, 200 OK."""
    lookup = _improvement_report_lookup(request)
    if lookup is None:
        # No lookup wired ⇒ documented "no lineage" signal.
        return canonical_json_response(
            {"strategy_id": strategy_id, "hypothesis_ids": []}
        )
    sid = StrategyId(strategy_id)
    report_opt = lookup.latest_for(sid)
    # Duck-type the Option — None / Nothing / object without
    # ``value`` ⇒ empty tuple.
    report = _unwrap_option(report_opt)
    if report is None:
        return canonical_json_response(
            {"strategy_id": strategy_id, "hypothesis_ids": []}
        )
    hypothesis_ids = list(getattr(report, "hypothesis_ids", ()) or ())
    return canonical_json_response(
        {
            "strategy_id": strategy_id,
            "hypothesis_ids": [str(h) for h in hypothesis_ids],
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unwrap_option(opt: object) -> object | None:
    """Duck-type the Option Protocol — handles Some(x), Nothing,
    None, and plain values uniformly."""
    if opt is None:
        return None
    # ``Nothing`` (no ``value`` attribute).
    if not hasattr(opt, "value"):
        # Could be a plain Ok or a value — return as-is.
        return opt
    inner = opt.value
    return inner
