"""``POST /api/registry/{strategy_id}/promote`` — port of the stdlib
webui's only mutation endpoint.

REQ refs:
- REQ_F_FAS_001 — route-for-route parity with the stdlib path.
- REQ_F_FAS_005 — Bearer + per-account-claim auth.
- REQ_NF_FAS_001 — same Err-category mapping as the stdlib path so
  canonical-JSON bodies are byte-identical.

The promotion semantics live in CR-008's ``RegistryRepository.request_promotion``;
this route is plumbing only (REQ_SDS_WEB_002 mirror — REQ_SDS_FAS_001
import-graph audit).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.responses import Response

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.notifications.payloads import AnomalyAlert
from trading_system.result import Err, Ok, Result
from trading_system.webapp.canonical import (
    canonical_error_response,
    canonical_json_response,
)
from trading_system.webui.schemas import PromoteResponse


router = APIRouter(prefix="/api/registry")


class PromoteRequest(BaseModel):
    """Body schema for ``POST /api/registry/{strategy_id}/promote``.

    Pydantic validation kicks in at the FastAPI boundary; malformed
    bodies surface as a 422 with the auto-generated detail. The
    Authorization header carries the Bearer token; the
    ``X-Operator-Token`` header carries the action-specific operator
    token (kept distinct so the audit row can pair both).
    """

    account_id: str = Field(min_length=1, description="Target account id")
    operator_id: str = Field(min_length=1, description="Acting operator id")
    rationale: str = Field(min_length=1, description="Promotion rationale")


@runtime_checkable
class RegistryPromoter(Protocol):
    """Read-only surface — CR-008 ``RegistryRepository`` satisfies."""

    def promote(
        self,
        *,
        strategy_id: StrategyId,
        operator_token: str,
        operator_id: str,
        rationale: str,
        account_id: AccountId,
    ) -> Result[None, str]: ...


@runtime_checkable
class PromotionAuditNotifier(Protocol):
    """Phase-A notifier — Phase B may swap in a structured-audit sink."""

    def dispatch(self, payload: AnomalyAlert) -> None: ...


_BEARER_PREFIX = "Bearer "


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX) :].strip() or None
    if auth:
        return auth.strip() or None
    legacy = request.headers.get("x-operator-token", "").strip()
    return legacy or None


def _operator_action_token(request: Request) -> str:
    return request.headers.get("x-operator-token", "").strip()


def _verifier(request: Request):
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:token_verifier_missing",
        )
    return verifier


def _promoter(request: Request) -> RegistryPromoter:
    promoter = getattr(request.app.state, "registry_promoter", None)
    if promoter is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:registry_promoter_missing",
        )
    return promoter


def _notifier(request: Request) -> PromotionAuditNotifier | None:
    return getattr(request.app.state, "promotion_audit_notifier", None)


def _status_for(reason: str) -> int:
    if reason == "registry:token_invalid":
        return status.HTTP_401_UNAUTHORIZED
    if reason == "registry:strategy_not_found":
        return status.HTTP_404_NOT_FOUND
    if reason == "registry:already_promoted":
        return status.HTTP_409_CONFLICT
    return status.HTTP_409_CONFLICT


@router.post(
    "/{strategy_id}/promote",
    response_class=Response,
    summary="Promote a registered strategy",
    responses={
        200: {"description": "Promotion accepted"},
        401: {"description": "Token invalid"},
        404: {"description": "Strategy not found"},
        409: {"description": "Already promoted or other categorised Err"},
    },
)
def post_promote(
    strategy_id: StrategyId,
    body: PromoteRequest,
    request: Request,
) -> Response:
    """REQ_F_FAS_001 / REQ_F_FAS_005 — mutation endpoint gated by the
    account-scoped operator token."""
    account_id = AccountId(body.account_id)

    # Auth — REQ_F_FAS_005.
    bearer = _extract_bearer(request)
    if bearer is None or not _verifier(request).verify(
        bearer, account_id=str(account_id)
    ):
        return canonical_error_response(
            "registry:token_invalid", status_code=status.HTTP_401_UNAUTHORIZED
        )

    operator_token = _operator_action_token(request)
    if not operator_token:
        return canonical_error_response(
            "webui:missing_operator_token", status_code=status.HTTP_400_BAD_REQUEST
        )

    # Delegate to the promoter.
    match _promoter(request).promote(
        strategy_id=strategy_id,
        operator_token=operator_token,
        operator_id=body.operator_id,
        rationale=body.rationale,
        account_id=account_id,
    ):
        case Err(reason):
            return canonical_error_response(reason, status_code=_status_for(reason))
        case Ok(_):
            pass

    # Audit fan-out — best-effort (matches the stdlib path).
    notifier = _notifier(request)
    if notifier is not None:
        notifier.dispatch(
            AnomalyAlert(
                code="webapp:registry_promotion",
                severity="INFO",
                account_id=account_id,
                message=f"strategy {strategy_id} validated by {body.operator_id}",
                at=datetime.now(UTC),
            )
        )

    return canonical_json_response(
        PromoteResponse(
            promoted=True,
            strategy_id=strategy_id,
            account_id=account_id,
        )
    )
