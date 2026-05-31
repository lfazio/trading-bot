"""Registry-list read endpoint — REQ_F_WEB_002 (c) / REQ_NF_WEB_002.

GET /accounts/<account_id>/registry returns the current
``RegistryListResponse`` for the account. The handler is a thin
adapter — the heavy lifting lives in a ``RegistryListReader``
Protocol so the route stays decoupled from the concrete persistence
repository (REQ_F_WEB_007 import-graph audit).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok
from trading_system.webui.auth import WebAuth
from trading_system.webui.schemas import (
    JsonResponse,
    RegistryListResponse,
)
from trading_system.webui.server import Request


@runtime_checkable
class RegistryListReader(Protocol):
    """Read-only surface for the registry-list endpoint."""

    def registry_list(
        self, *, account_id: AccountId, as_of: datetime
    ) -> RegistryListResponse: ...


def build_registry_list_handler(*, auth: WebAuth, reader: RegistryListReader):
    """Returns a handler closure ``(Request) -> JsonResponse``.

    See ``summary.py::build_summary_handler`` for the full contract.
    Path shape: ``/accounts/<aid>/registry``.
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
        payload = reader.registry_list(
            account_id=account_id,
            as_of=_default_now(),
        )
        return JsonResponse.from_canonical(payload)

    return handle


def _parse_account_id_from_path(path: str) -> AccountId | None:
    """Path shape: ``/accounts/<aid>/registry``."""
    parts = path.strip("/").split("/")
    if (
        len(parts) != 3
        or parts[0] != "accounts"
        or parts[2] != "registry"
        or not parts[1].strip()
    ):
        return None
    return AccountId(parts[1])


def _default_now() -> datetime:
    return datetime.now(tz=UTC)
