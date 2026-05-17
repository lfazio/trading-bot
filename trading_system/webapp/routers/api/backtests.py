"""``POST /api/backtests`` + ``GET /api/backtests`` + ``GET /api/backtests/{job_id}``
+ ``GET /events/backtests/{job_id}``.

REQ refs:
- REQ_F_FAS_006 — async backtest invocation; 202 + job_id; SSE
  progress stream.
- REQ_SDD_FAS_005 — InProcessJobQueue ProcessPoolExecutor; the
  route never blocks on the worker.
- REQ_NF_WEB_001 — HTTP crash SHALL NOT propagate to trading
  (child-process isolation via the executor).
- REQ_NF_FAS_001 — read responses go through the canonical-JSON
  helper so the FastAPI bytes match the stdlib path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.responses import Response

from trading_system.notifications.canonical import canonical_json_line
from trading_system.result import Err, Nothing, Some
from trading_system.webapp.auth_deps import RequestRequireHousehold
from trading_system.webapp.canonical import (
    canonical_error_response,
    canonical_json_response,
)
from trading_system.webapp.job_queue import (
    JobQueue,
    JobSpec,
    JobState,
    new_job_id,
)


router = APIRouter()


def _queue(request: Request) -> JobQueue:
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:job_queue_missing",
        )
    return queue


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class BacktestSubmitRequest(BaseModel):
    """Body shape for ``POST /api/backtests``.

    Mirrors the CLI's ``trading-bot backtest`` flags so operators
    can drive a remote run with the same knobs they'd type locally.
    """

    config_dir: str = Field(default="config", description="config directory inside the container/host")
    start: datetime = Field(description="backtest start (ISO-8601 with timezone)")
    end: datetime = Field(description="backtest end (ISO-8601 with timezone)")
    with_slippage: bool = Field(default=False)
    account_id: str = Field(default="default")


def _state_to_dict(state: JobState) -> dict[str, object]:
    return {
        "job_id": state.job_id,
        "status": state.status.value,
        "submitted_at": state.submitted_at.isoformat(),
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "completed_at": (
            state.completed_at.isoformat() if state.completed_at else None
        ),
        "error_category": state.error_category,
        "summary": state.summary,
    }


# ---------------------------------------------------------------------------
# POST /api/backtests
# ---------------------------------------------------------------------------


@router.post(
    "/api/backtests",
    response_class=Response,
    summary="Submit an async backtest",
    description=(
        "Returns 202 + job_id. The worker runs in a separate process "
        "(REQ_NF_WEB_001 child-process isolation). Poll "
        "GET /api/backtests/{job_id} or subscribe to "
        "GET /events/backtests/{job_id} for progress."
    ),
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_backtest(
    body: BacktestSubmitRequest,
    request: RequestRequireHousehold,
) -> Response:
    queue = _queue(request)
    spec = JobSpec(
        job_id=new_job_id(),
        config_dir=body.config_dir,
        start=body.start,
        end=body.end,
        with_slippage=body.with_slippage,
        account_id=body.account_id,
    )
    result = await queue.submit(spec)
    if isinstance(result, Err):
        return canonical_error_response(
            result.error, status_code=status.HTTP_409_CONFLICT
        )
    return canonical_json_response(
        {
            "job_id": result.value,
            "submitted_at": datetime.now(UTC).isoformat(),
            "status_url": f"/api/backtests/{result.value}",
            "stream_url": f"/events/backtests/{result.value}",
        },
        status_code=status.HTTP_202_ACCEPTED,
    )


# ---------------------------------------------------------------------------
# GET /api/backtests + GET /api/backtests/{job_id}
# ---------------------------------------------------------------------------


@router.get(
    "/api/backtests",
    response_class=Response,
    summary="List submitted backtests",
)
def list_backtests(request: RequestRequireHousehold) -> Response:
    queue = _queue(request)
    return canonical_json_response(
        {"jobs": [_state_to_dict(s) for s in queue.all()]}
    )


@router.get(
    "/api/backtests/{job_id}",
    response_class=Response,
    summary="Read a single backtest's status + summary",
)
def get_backtest(
    job_id: str,
    request: RequestRequireHousehold,
) -> Response:
    queue = _queue(request)
    match queue.status(job_id):
        case Some(state):
            return canonical_json_response(_state_to_dict(state))
        case Nothing():
            return canonical_error_response(
                f"job:not_found:{job_id}",
                status_code=status.HTTP_404_NOT_FOUND,
            )
    return canonical_error_response(
        f"job:not_found:{job_id}", status_code=status.HTTP_404_NOT_FOUND
    )


# ---------------------------------------------------------------------------
# GET /events/backtests/{job_id} — SSE progress stream
# ---------------------------------------------------------------------------


@router.get(
    "/events/backtests/{job_id}",
    summary="SSE progress stream for a backtest job",
)
async def stream_backtest(
    job_id: str,
    request: RequestRequireHousehold,
) -> EventSourceResponse:
    queue = _queue(request)

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        async for state in queue.stream(job_id):
            if await request.is_disconnected():
                return
            yield {
                "id": state.submitted_at.isoformat(),
                "event": state.status.value,
                "data": canonical_json_line(_state_to_dict(state)),
            }

    return EventSourceResponse(event_generator())
