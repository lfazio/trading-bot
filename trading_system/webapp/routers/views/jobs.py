"""HTMX backtest-jobs pages.

Four routes, all HTML-only — the JSON API at ``/api/backtests`` is
the canonical machine surface; this view layer renders the same
JobQueue state as Jinja-rendered HTML for the browser.

- ``GET /jobs`` — list page (auth gate; redirects to ``/login`` when
  the household cookie/header is missing or invalid, mirroring the
  dashboard's browser-friendly path).
- ``GET /jobs/partial`` — table fragment for HTMX swap (auth gate
  returns JSON 401 — fragments aren't a browser entry point).
- ``POST /jobs/submit`` — consumes the HTML form and enqueues a job
  via the same ``JobQueue`` Protocol the JSON API uses; returns the
  freshly-rendered table fragment so the form swap shows the new
  row immediately.
- ``GET /jobs/{job_id}`` — single-job detail page with SSE-streamed
  progress.

Route-registration order matters: ``/jobs/partial`` and
``/jobs/submit`` are registered BEFORE the catch-all
``/jobs/{job_id}`` so FastAPI's first-match dispatch routes them
correctly. The detail route still cleanly rejects literal ids
``partial`` and ``submit`` via the explicit static routes above it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse, RedirectResponse

from trading_system.accounts.token_verifier import HOUSEHOLD_CLAIM
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.webapp.auth_deps import (
    RequestRequireHousehold,
    _extract_token,
)
from trading_system.webapp.job_queue import JobQueue, JobSpec, JobState, new_job_id


router = APIRouter()


def _templates(request: Request) -> Jinja2Templates:
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise RuntimeError("webapp:templates_missing")
    return templates


def _queue(request: Request) -> JobQueue:
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:job_queue_missing",
        )
    return queue


def _state_to_view(state: JobState) -> dict[str, object]:
    """Flatten ``JobState`` into a template-friendly dict — datetimes
    pre-rendered as ISO-8601 strings + summary defaulted to an empty
    dict so Jinja's ``{% if summary %}`` branch is well-defined."""
    return {
        "job_id": state.job_id,
        "status": state.status.value,
        "submitted_at": state.submitted_at.isoformat(timespec="seconds"),
        "started_at": (
            state.started_at.isoformat(timespec="seconds")
            if state.started_at
            else None
        ),
        "completed_at": (
            state.completed_at.isoformat(timespec="seconds")
            if state.completed_at
            else None
        ),
        "error_category": state.error_category,
        "summary": state.summary or {},
    }


# ---------------------------------------------------------------------------
# GET /jobs — full page
# ---------------------------------------------------------------------------


@router.get(
    "/jobs",
    response_class=HTMLResponse,
    name="jobs",
    response_model=None,
)
def get_jobs_page(request: Request) -> HTMLResponse | RedirectResponse:
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verifier.verify(token, account_id=HOUSEHOLD_CLAIM)
    ):
        return RedirectResponse(url="/login", status_code=303)
    return _templates(request).TemplateResponse(
        request=request,
        name="jobs.html",
        context={
            "account_id": "default",
            "jobs": [_state_to_view(s) for s in _queue(request).all()],
        },
    )


# ---------------------------------------------------------------------------
# GET /jobs/partial — table fragment for HTMX swap
# ---------------------------------------------------------------------------


@router.get("/jobs/partial", response_class=HTMLResponse, name="jobs-partial")
def get_jobs_partial(request: RequestRequireHousehold) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request=request,
        name="partials/jobs_table.html",
        context={
            "jobs": [_state_to_view(s) for s in _queue(request).all()],
        },
    )


# ---------------------------------------------------------------------------
# POST /jobs/submit — form submission
# ---------------------------------------------------------------------------


@router.post("/jobs/submit", response_class=HTMLResponse, name="jobs-submit")
async def post_jobs_submit(
    request: RequestRequireHousehold,
    config_dir: Annotated[str, Form()],
    start: Annotated[str, Form()],
    end: Annotated[str, Form()],
    with_slippage: Annotated[str | None, Form()] = None,
    account_id: Annotated[str, Form()] = "default",
) -> HTMLResponse:
    queue = _queue(request)
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError:
        return _render_partial_with_error(
            request, queue, "webapp:bad_form:iso_datetime"
        )
    spec = JobSpec(
        job_id=new_job_id(),
        config_dir=config_dir,
        start=start_dt,
        end=end_dt,
        with_slippage=with_slippage == "on",
        account_id=account_id,
    )
    match await queue.submit(spec):
        case Ok(_):
            return _render_partial(request, queue)
        case Err(reason):
            return _render_partial_with_error(request, queue, reason)


def _render_partial(request: Request, queue: JobQueue) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request=request,
        name="partials/jobs_table.html",
        context={
            "jobs": [_state_to_view(s) for s in queue.all()],
        },
    )


def _render_partial_with_error(
    request: Request, queue: JobQueue, reason: str
) -> HTMLResponse:
    """Re-render the partial WITH the error embedded above the table.

    Keeps the swap target (`#jobs-table`) consistent so the operator
    sees both the rejection reason AND the unchanged job list in
    one response. Cheaper than maintaining two swap targets."""
    return _templates(request).TemplateResponse(
        request=request,
        name="partials/jobs_table.html",
        context={
            "jobs": [_state_to_view(s) for s in queue.all()],
            "submit_error": reason,
        },
    )


# ---------------------------------------------------------------------------
# GET /jobs/{job_id} — single-job detail page
#
# Registered LAST so the static routes (/jobs, /jobs/partial,
# /jobs/submit) win first-match in FastAPI's dispatch.
# ---------------------------------------------------------------------------


@router.get(
    "/jobs/{job_id}",
    response_class=HTMLResponse,
    name="job-detail",
    response_model=None,
)
def get_job_detail(
    job_id: str, request: Request
) -> HTMLResponse | RedirectResponse:
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verifier.verify(token, account_id=HOUSEHOLD_CLAIM)
    ):
        return RedirectResponse(url="/login", status_code=303)
    queue = _queue(request)
    match queue.status(job_id):
        case Some(state):
            return _templates(request).TemplateResponse(
                request=request,
                name="job_detail.html",
                context={
                    "account_id": "default",
                    "job": _state_to_view(state),
                },
            )
        case Nothing():
            return _templates(request).TemplateResponse(
                request=request,
                name="job_detail_missing.html",
                context={
                    "account_id": "default",
                    "job_id": job_id,
                },
                status_code=404,
            )
