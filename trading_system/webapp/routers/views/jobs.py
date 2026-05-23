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

from trading_system.result import Err, Nothing, Ok, Some
from trading_system.webapp.auth_deps import (
    RequestRequireAnyValidClaim,
    _extract_token,
    verify_any_valid_claim,
)
from trading_system.webapp.fragments import fragment_context
from trading_system.webapp.job_queue import JobQueue, JobSpec, JobState, new_job_id


router = APIRouter()


# REQ_F_WEB2_004 — per-job prefill cache so a completed row's
# "Rerun" button can re-render the form with the exact prior
# inputs. Module-level so it survives across requests in the
# single-process webapp deploy. Bounded informally by the
# in-process JobQueue's own bound (queue + cache evict together
# at shutdown). Backed by the same lifetime as the job queue.
_PREFILL_CACHE: dict[str, dict[str, str]] = {}


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
        "prefill": _PREFILL_CACHE.get(state.job_id, {}),
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
        or not verify_any_valid_claim(verifier, token)
    ):
        return RedirectResponse(url="/login", status_code=303)
    # REQ_F_WEB2_004 — "rerun" pre-fills the form via query-string
    # parameters. The dashboard's "Rerun" button on each completed
    # row links here with start/end/universe/with_slippage; the
    # operator can also bookmark a particular setup.
    q = request.query_params
    prefill = {
        "config_dir": q.get("config_dir", "config"),
        "start": q.get("start", "2024-01-02T00:00:00+00:00"),
        "end": q.get("end", "2024-12-31T00:00:00+00:00"),
        "universe": q.get("universe", "eu-dividend-starter"),
        "with_slippage": q.get("with_slippage", "").lower() == "on",
    }
    return _templates(request).TemplateResponse(
        request=request,
        name="jobs.html",
        context={
            "account_id": "default",
            "jobs": [_state_to_view(s) for s in _queue(request).all()],
            "prefill": prefill,
            # Closed set matching the wizard's allow-list — adding
            # a new entry is a deliberate code change here + a
            # wiki amendment.
            "allowed_universes": ("eu-dividend-starter", "cac40"),
            **fragment_context(request),
        },
    )


# ---------------------------------------------------------------------------
# GET /jobs/partial — table fragment for HTMX swap
# ---------------------------------------------------------------------------


@router.get("/jobs/partial", response_class=HTMLResponse, name="jobs-partial")
def get_jobs_partial(request: RequestRequireAnyValidClaim) -> HTMLResponse:
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
    request: RequestRequireAnyValidClaim,
    config_dir: Annotated[str, Form()],
    start: Annotated[str, Form()],
    end: Annotated[str, Form()],
    with_slippage: Annotated[str | None, Form()] = None,
    account_id: Annotated[str, Form()] = "default",
    universe: Annotated[str, Form()] = "eu-dividend-starter",
) -> HTMLResponse:
    queue = _queue(request)
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError:
        return _render_partial_with_error(
            request, queue, "webapp:bad_form:iso_datetime"
        )
    if start_dt >= end_dt:
        return _render_partial_with_error(
            request, queue, "webapp:bad_form:start_after_end"
        )
    if universe not in ("eu-dividend-starter", "cac40"):
        return _render_partial_with_error(
            request, queue, f"webapp:bad_form:unknown_universe:{universe}"
        )
    spec = JobSpec(
        job_id=new_job_id(),
        config_dir=config_dir,
        start=start_dt,
        end=end_dt,
        with_slippage=with_slippage == "on",
        account_id=account_id,
    )
    # Universe is a label (the underlying main.run reads it from
    # config_dir); we surface it on each row via the prefill cache
    # so the "Rerun" button can replay the exact form state.
    _PREFILL_CACHE[spec.job_id] = {
        "config_dir": config_dir,
        "start": start,
        "end": end,
        "universe": universe,
        "with_slippage": "on" if with_slippage == "on" else "",
    }
    # Detect HTMX requests by the documented HX-Request header.
    # Browsers without the HTMX runtime get a regular 303 -> /jobs
    # so the page navigates naturally.
    is_htmx = request.headers.get("HX-Request") == "true"

    match await queue.submit(spec):
        case Ok(_):
            if is_htmx:
                return _render_partial(request, queue)
            return RedirectResponse(url="/jobs", status_code=303)  # type: ignore[return-value]
        case Err(reason):
            if is_htmx:
                return _render_partial_with_error(request, queue, reason)
            # Non-HTMX: stay on /jobs and render the page with
            # the error banner so the operator sees what went wrong.
            return _templates(request).TemplateResponse(
                request=request,
                name="jobs.html",
                context={
                    "account_id": "default",
                    "jobs": [_state_to_view(s) for s in queue.all()],
                    "prefill": {
                        "config_dir": config_dir,
                        "start": start,
                        "end": end,
                        "universe": universe,
                        "with_slippage": with_slippage == "on",
                    },
                    "allowed_universes": ("eu-dividend-starter", "cac40"),
                    "submit_error": reason,
                    **fragment_context(request),
                },
                status_code=400,
            )


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
        or not verify_any_valid_claim(verifier, token)
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
                    **fragment_context(request),
                },
            )
        case Nothing():
            return _templates(request).TemplateResponse(
                request=request,
                name="job_detail_missing.html",
                context={
                    "account_id": "default",
                    "job_id": job_id,
                    **fragment_context(request),
                },
                status_code=404,
            )
