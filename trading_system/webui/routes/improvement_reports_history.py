"""Improvement-reports-history read endpoint — REQ_F_WEB_002 (e) / REQ_NF_WEB_002.

GET /accounts/<account_id>/improvement-reports returns the
``ImprovementReportsHistoryResponse`` for the account. The handler
is a thin adapter — the heavy lifting lives in an
``ImprovementReportsHistoryReader`` Protocol so the route stays
decoupled from the concrete meta-loop types (REQ_F_WEB_007).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok
from trading_system.webui.auth import WebAuth
from trading_system.webui.schemas import (
    ImprovementReportsHistoryResponse,
    JsonResponse,
)
from trading_system.webui.server import Request


@runtime_checkable
class ImprovementReportsHistoryReader(Protocol):
    """Read-only surface for the improvement-reports-history
    endpoint."""

    def improvement_reports_history(
        self, *, account_id: AccountId, as_of: datetime
    ) -> ImprovementReportsHistoryResponse: ...


def build_improvement_reports_history_handler(
    *, auth: WebAuth, reader: ImprovementReportsHistoryReader
):
    """Returns a handler closure ``(Request) -> JsonResponse``.

    Path shape: ``/accounts/<aid>/improvement-reports``. Same
    auth + method contract as the other webui read endpoints.
    """

    def handle(request: Request) -> JsonResponse:
        if request.method != "GET":
            return JsonResponse.error(
                405, f"webui:method_not_allowed:{request.method}"
            )
        account_id = _parse_account_id_from_path(request.path)
        if account_id is None:
            return JsonResponse.error(400, "webui:bad_path")
        match auth.require_household(request.headers):
            case Err(reason):
                return JsonResponse.error(401, reason)
            case Ok(_):
                pass
        payload = reader.improvement_reports_history(
            account_id=account_id,
            as_of=_default_now(),
        )
        return JsonResponse.from_canonical(payload)

    return handle


def _parse_account_id_from_path(path: str) -> AccountId | None:
    """Path shape: ``/accounts/<aid>/improvement-reports``."""
    parts = path.strip("/").split("/")
    if (
        len(parts) != 3
        or parts[0] != "accounts"
        or parts[2] != "improvement-reports"
        or not parts[1].strip()
    ):
        return None
    return AccountId(parts[1])


def _default_now() -> datetime:
    return datetime.now(tz=UTC)
