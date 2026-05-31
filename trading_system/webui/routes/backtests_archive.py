"""Backtest-archive read endpoint — REQ_F_WEB_002 (d) / REQ_NF_WEB_002.

GET /accounts/<account_id>/backtests returns a paginated
``BacktestsArchiveResponse`` for the account. The handler is a
thin adapter — the heavy lifting lives in a
``BacktestsArchiveReader`` Protocol so the route stays decoupled
from the concrete persistence repository (REQ_F_WEB_007).

Query params:
- ``?per_page=<n>`` (default 25, capped at 100).
- ``?page=<n>`` (default 1; one-indexed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit

from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok
from trading_system.webui.auth import WebAuth
from trading_system.webui.schemas import (
    BacktestsArchiveResponse,
    JsonResponse,
)
from trading_system.webui.server import Request


_DEFAULT_PER_PAGE = 25
_MAX_PER_PAGE = 100


@runtime_checkable
class BacktestsArchiveReader(Protocol):
    """Read-only surface for the backtest-archive endpoint."""

    def backtests_archive(
        self,
        *,
        account_id: AccountId,
        as_of: datetime,
        per_page: int,
        page: int,
    ) -> BacktestsArchiveResponse: ...


def build_backtests_archive_handler(
    *, auth: WebAuth, reader: BacktestsArchiveReader
):
    """Returns a handler closure ``(Request) -> JsonResponse``.

    Path shape: ``/accounts/<aid>/backtests``. ``per_page`` /
    ``page`` query params clamp to the documented bounds; out-of-
    range values surface as 400.
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
        pagination = _parse_pagination(request)
        if isinstance(pagination, Err):
            return JsonResponse.error(400, pagination.error)
        per_page, page = pagination.value
        payload = reader.backtests_archive(
            account_id=account_id,
            as_of=_default_now(),
            per_page=per_page,
            page=page,
        )
        return JsonResponse.from_canonical(payload)

    return handle


def _parse_account_id_from_path(path: str) -> AccountId | None:
    """Path shape: ``/accounts/<aid>/backtests``."""
    parsed = urlsplit(path)
    parts = parsed.path.strip("/").split("/")
    if (
        len(parts) != 3
        or parts[0] != "accounts"
        or parts[2] != "backtests"
        or not parts[1].strip()
    ):
        return None
    return AccountId(parts[1])


def _parse_pagination(request: Request):
    qs = parse_qs(urlsplit(request.path).query)
    per_page_raw = qs.get("per_page", [None])[0]
    page_raw = qs.get("page", [None])[0]
    per_page = _DEFAULT_PER_PAGE
    page = 1
    if per_page_raw is not None:
        try:
            per_page = int(per_page_raw)
        except ValueError:
            return Err("webui:bad_per_page")
        if per_page < 1 or per_page > _MAX_PER_PAGE:
            return Err(f"webui:per_page_out_of_bounds:{per_page}")
    if page_raw is not None:
        try:
            page = int(page_raw)
        except ValueError:
            return Err("webui:bad_page")
        if page < 1:
            return Err(f"webui:page_out_of_bounds:{page}")
    return Ok((per_page, page))


def _default_now() -> datetime:
    return datetime.now(tz=UTC)
