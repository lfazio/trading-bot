"""Registry-promotion mutation — REQ_F_WEB_004 / REQ_SDS_WEB_002.

The **only** mutation endpoint the web UI exposes (REQ_F_WEB_004).
Idempotency, auth, and notification fan-out all happen here; the
underlying promotion semantics live in
``RegistryRepository.request_promotion`` — the HTTP path SHALL NOT
inline them (REQ_SDS_WEB_002).

Flow:
  1. Idempotency check (REQ_F_WEB_008) — replay-safe.
  2. Per-account auth (REQ_F_WEB_005 / REQ_SDD_ACC_007).
  3. Delegate to ``promoter.promote(...)`` — Protocol-shaped so the
     route stays free of CR-008's concrete RegistryRepository.
  4. On success, fire an ``AnomalyAlert`` through the fan-out
     (REQ_F_WEB_006) — Phase-B-ready hook.
  5. Build canonical response + record idempotency.

The categorised Err mapping mirrors the CR-008 audit log:
  registry:token_invalid          → 401
  registry:already_promoted       → 409
  registry:strategy_not_found     → 404
  anything else                   → 409 (operator must triage)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.notifications.fanout import NotificationFanOut
from trading_system.notifications.payloads import AnomalyAlert
from trading_system.result import Err, Nothing, Ok, Result, Some
from trading_system.webui.auth import WebAuth
from trading_system.webui.idempotency import IdempotencyStore
from trading_system.webui.schemas import (
    JsonResponse,
    PromoteResponse,
)
from trading_system.webui.server import Request


@runtime_checkable
class RegistryPromoter(Protocol):
    """Read-only surface the route uses; the CR-008
    ``RegistryRepository`` Phase-B satisfies it structurally."""

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
    """Phase-A notifier — the route fires an AnomalyAlert through
    a NotificationFanOut. Phase B may swap in a structured-audit
    sink. The Protocol surface stays stable."""

    def dispatch(self, payload: AnomalyAlert) -> None: ...


def build_promotion_handler(
    *,
    auth: WebAuth,
    promoter: RegistryPromoter,
    idempotency: IdempotencyStore,
    notifier: NotificationFanOut | PromotionAuditNotifier,
):
    """Returns a handler closure for ``POST /strategies/<sid>/promote``.

    The handler body is intentionally linear — the test suite drives
    every branch with simple fixtures.
    """

    def handle(request: Request) -> JsonResponse:
        if request.method != "POST":
            return JsonResponse.error(
                405, f"webui:method_not_allowed:{request.method}"
            )
        strategy_id = _parse_strategy_id_from_path(request.path)
        if strategy_id is None:
            return JsonResponse.error(400, "webui:bad_path")
        body = request.json()
        account_id_raw = body.get("account_id")
        operator_id = body.get("operator_id")
        rationale = body.get("rationale")
        if not (
            isinstance(account_id_raw, str)
            and isinstance(operator_id, str)
            and isinstance(rationale, str)
            and account_id_raw.strip()
            and operator_id.strip()
            and rationale.strip()
        ):
            return JsonResponse.error(400, "webui:bad_request_body")
        account_id = AccountId(account_id_raw)

        # ----- Idempotency check (REQ_F_WEB_008) ------------------------
        idem_key = request.headers.get("Idempotency-Key", "")
        if idem_key:
            match idempotency.lookup(account_id=account_id, key=idem_key):
                case Err(reason):
                    return JsonResponse.error(500, f"webui:idempotency:{reason}")
                case Ok(Some(prior_body)):
                    return JsonResponse(status_code=200, body=prior_body)
                case Ok(Nothing()):
                    pass

        # ----- Auth (REQ_F_WEB_005 / REQ_SDD_ACC_007) -------------------
        match auth.require_account(request.headers, account_id):
            case Err(reason):
                return JsonResponse.error(401, reason)
            case Ok(_):
                pass

        # ----- Extract operator token (after auth so we don't leak it
        # to an unauth'd caller via timing). --------------------------
        operator_token = _extract_operator_token(request.headers)
        if not operator_token:
            return JsonResponse.error(400, "webui:missing_operator_token")

        # ----- Delegate to the promoter --------------------------------
        match promoter.promote(
            strategy_id=strategy_id,
            operator_token=operator_token,
            operator_id=operator_id,
            rationale=rationale,
            account_id=account_id,
        ):
            case Err(reason):
                return JsonResponse.error(_status_for(reason), reason)
            case Ok(_):
                pass

        # ----- Notify (REQ_F_WEB_006) ----------------------------------
        notifier.dispatch(
            AnomalyAlert(
                code="webui:registry_promotion",
                severity="INFO",
                account_id=account_id,
                message=(
                    f"strategy {strategy_id} validated by {operator_id}"
                ),
                at=_default_now(),
            )
        )

        # ----- Canonical response + idempotency record -----------------
        response = JsonResponse.from_canonical(
            PromoteResponse(
                promoted=True,
                strategy_id=strategy_id,
                account_id=account_id,
            )
        )
        if idem_key:
            idempotency.record(
                account_id=account_id,
                key=idem_key,
                body=response.body,
                status_code=response.status_code,
            )
        return response

    return handle


def _parse_strategy_id_from_path(path: str) -> StrategyId | None:
    """Path shape: ``/strategies/<sid>/promote``."""
    parts = path.strip("/").split("/")
    if (
        len(parts) != 3
        or parts[0] != "strategies"
        or parts[2] != "promote"
        or not parts[1].strip()
    ):
        return None
    return StrategyId(parts[1])


def _extract_operator_token(headers) -> str:
    """Look for the operator-action token in the dedicated header
    (kept distinct from the Bearer auth token so the audit row can
    pair both — Phase-B persistence stores ``sha256(operator_token)``,
    not the bearer auth token)."""
    for key in ("X-Operator-Token", "x-operator-token"):
        v = headers.get(key)
        if v is not None and v.strip():
            return v.strip()
    return ""


def _status_for(reason: str) -> int:
    if reason == "registry:token_invalid":
        return 401
    if reason == "registry:strategy_not_found":
        return 404
    if reason == "registry:already_promoted":
        return 409
    return 409


def _default_now() -> datetime:
    return datetime.now(tz=UTC)
