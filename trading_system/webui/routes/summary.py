"""Financial-summary read endpoint — REQ_F_WEB_002 (b) / REQ_NF_WEB_002.

GET /accounts/<account_id>/summary returns the current
``SummaryResponse`` for the account. The handler is a thin
adapter — the heavy lifting lives in a ``SummaryReader`` Protocol
so the route stays decoupled from the concrete analytics types
(REQ_F_WEB_007 import-graph audit).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok
from trading_system.webui.auth import WebAuth
from trading_system.webui.schemas import JsonResponse, SummaryResponse
from trading_system.webui.server import Request


@runtime_checkable
class SummaryReader(Protocol):
    """Read-only surface the handler asks for the current summary."""

    def summary(
        self, *, account_id: AccountId, as_of: datetime
    ) -> SummaryResponse: ...


def build_summary_handler(*, auth: WebAuth, reader: SummaryReader):
    """Returns a handler closure ``(Request) -> JsonResponse``.

    The handler:
      1. Parses ``account_id`` from the URL path; a malformed
         path returns a 400 with ``webui:bad_path``.
      2. Verifies a household-claim token via ``WebAuth``.
         A bad token returns ``401 registry:token_invalid``.
      3. Calls ``reader.summary`` and wraps the result through
         the canonical JSON path (REQ_NF_WEB_002 — byte-identical
         replay for identical inputs).
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
        payload = reader.summary(
            account_id=account_id,
            as_of=_default_now(),
        )
        return JsonResponse.from_canonical(payload)

    return handle


def _parse_account_id_from_path(path: str) -> AccountId | None:
    """Path shape: ``/accounts/<aid>/summary``."""
    parts = path.strip("/").split("/")
    if (
        len(parts) != 3
        or parts[0] != "accounts"
        or parts[2] != "summary"
        or not parts[1].strip()
    ):
        return None
    return AccountId(parts[1])


def _default_now() -> datetime:
    return datetime.now(tz=UTC)
