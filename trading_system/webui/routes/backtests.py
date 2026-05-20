"""Stdlib webui async-backtest routes — CR-004 Phase B
(REQ_F_WEB_003 / REQ_F_WEB_009 / REQ_SDD_WEB_005 / REQ_SDS_WEB_003).

Two endpoints:
- ``POST /accounts/<aid>/backtests`` — enqueue a backtest job;
  returns ``202 Accepted`` + ``{"job_id": ..., "status_url": ...}``.
- ``GET /accounts/<aid>/backtests/<job_id>`` — read the current
  status + summary (when completed).

Plumbing only — the heavy lifting lives behind the
``JobQueue`` Protocol; the route file SHALL NOT import a
concrete queue (REQ_SDD_WEB_006 routes audit). The queue is
operator-wired at server construction; the closure here
captures it via ``build_*_handler``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId
from trading_system.notifications.canonical import canonical_json_line
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.webui.auth import WebAuth
from trading_system.webui.job_queue import (
    BacktestJobSpec,
    JobQueue,
    JobStatus,
)
from trading_system.webui.schemas import JsonResponse
from trading_system.webui.server import Request


@runtime_checkable
class JobIdGenerator(Protocol):
    """Closed surface for generating a fresh job id. The default
    implementation in ``build_submit_handler`` uses ``uuid.uuid4``;
    operators can inject a deterministic generator for tests."""

    def __call__(self) -> str: ...


def build_submit_handler(
    *,
    auth: WebAuth,
    queue: JobQueue,
    id_generator: JobIdGenerator | None = None,
):
    """Build the ``POST /accounts/<aid>/backtests`` handler closure.

    The handler:
      1. Parses ``account_id`` from the URL path; bad shape ⇒ 400.
      2. Verifies a household-claim token (read endpoints scope —
         backtest submission produces no live-mutation effect
         until the worker completes).
      3. Validates the body's start/end/with_slippage/config_dir
         fields; bad body ⇒ 400 with the categorised reason.
      4. Submits the spec via ``queue.submit`` — returns
         ``202 Accepted`` with the job_id + status_url on Ok.
    """
    import uuid

    def fresh_id() -> str:
        return uuid.uuid4().hex

    gen = id_generator if id_generator is not None else fresh_id

    def handle(request: Request) -> JsonResponse:
        if request.method != "POST":
            return JsonResponse.error(
                405, f"webui:method_not_allowed:{request.method}"
            )
        account_id = _parse_account_id(request.path)
        if account_id is None:
            return JsonResponse.error(400, "webui:bad_path")
        match auth.require_household(request.headers):
            case Err(reason):
                return JsonResponse.error(401, reason)
            case Ok(_):
                pass
        body = request.json()
        spec_or_err = _parse_spec(body, job_id=gen(), account_id=account_id)
        match spec_or_err:
            case Err(reason):
                return JsonResponse.error(400, reason)
            case Ok(spec):
                pass
        match queue.submit(spec):
            case Err(reason):
                return JsonResponse.error(409, reason)
            case Ok(job_id):
                return JsonResponse.from_canonical(
                    {
                        "job_id": job_id,
                        "submitted_at": datetime.now(UTC).isoformat(),
                        "status_url": (
                            f"/accounts/{account_id}/backtests/{job_id}"
                        ),
                    },
                    status_code=202,
                )

    return handle


def build_status_handler(*, auth: WebAuth, queue: JobQueue):
    """Build the ``GET /accounts/<aid>/backtests/<job_id>`` handler
    closure.

    Returns:
      - ``200`` with the canonical-JSON state when the job exists.
      - ``404 webui:job_not_found:<id>`` when it doesn't.
      - ``401`` on auth failure.
    """

    def handle(request: Request) -> JsonResponse:
        if request.method != "GET":
            return JsonResponse.error(
                405, f"webui:method_not_allowed:{request.method}"
            )
        parsed = _parse_account_and_job(request.path)
        if parsed is None:
            return JsonResponse.error(400, "webui:bad_path")
        _, job_id = parsed
        match auth.require_household(request.headers):
            case Err(reason):
                return JsonResponse.error(401, reason)
            case Ok(_):
                pass
        match queue.status(job_id):
            case Err(reason):
                return JsonResponse.error(500, reason)
            case Ok(Nothing()):
                return JsonResponse.error(404, f"webui:job_not_found:{job_id}")
            case Ok(Some(state)):
                payload = {
                    "job_id": state.job_id,
                    "status": state.status.value,
                    "submitted_at": state.submitted_at.isoformat(),
                    "started_at": (
                        state.started_at.isoformat() if state.started_at else None
                    ),
                    "completed_at": (
                        state.completed_at.isoformat()
                        if state.completed_at
                        else None
                    ),
                    "error_category": state.error_category,
                    "summary": dict(state.summary),
                }
                return JsonResponse(
                    status_code=200, body=canonical_json_line(payload)
                )

    return handle


# ---------------------------------------------------------------------------
# Path + body parsing
# ---------------------------------------------------------------------------


def _parse_account_id(path: str) -> AccountId | None:
    """Path shape: ``/accounts/<aid>/backtests``."""
    parts = path.strip("/").split("/")
    if (
        len(parts) != 3
        or parts[0] != "accounts"
        or parts[2] != "backtests"
        or not parts[1].strip()
    ):
        return None
    return AccountId(parts[1])


def _parse_account_and_job(path: str) -> tuple[AccountId, str] | None:
    """Path shape: ``/accounts/<aid>/backtests/<job_id>``."""
    parts = path.strip("/").split("/")
    if (
        len(parts) != 4
        or parts[0] != "accounts"
        or parts[2] != "backtests"
        or not parts[1].strip()
        or not parts[3].strip()
    ):
        return None
    return AccountId(parts[1]), parts[3]


def _parse_spec(
    body: object,
    *,
    job_id: str,
    account_id: AccountId,
):
    """Validate the JSON body + assemble a ``BacktestJobSpec``.

    Required fields: ``config_dir`` (str), ``start`` (ISO datetime
    string), ``end`` (ISO datetime string). Optional:
    ``with_slippage`` (bool, default False).
    """
    if not isinstance(body, Mapping):
        return Err("webui:bad_request_body")
    config_dir = body.get("config_dir")
    start_raw = body.get("start")
    end_raw = body.get("end")
    with_slippage = body.get("with_slippage", False)
    if not isinstance(config_dir, str) or not config_dir.strip():
        return Err("webui:bad_request_body:config_dir")
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        return Err("webui:bad_request_body:start_end")
    try:
        start = datetime.fromisoformat(start_raw)
        end = datetime.fromisoformat(end_raw)
    except ValueError:
        return Err("webui:bad_request_body:iso_datetime")
    if not isinstance(with_slippage, bool):
        return Err("webui:bad_request_body:with_slippage")
    return Ok(
        BacktestJobSpec(
            job_id=job_id,
            config_dir=config_dir,
            start=start,
            end=end,
            with_slippage=with_slippage,
            account_id=str(account_id),
        )
    )


# Importing JobStatus here just to keep the public symbol reachable
# for callers that build handler decorators; the static type checker
# warns when an import is unused only inside the function body so the
# top-level reference keeps it visible without dead-code suppression.
_ = JobStatus
