"""Reports panel — REQ_F_WEB2_005.

Surfaces the CR-016 5-file report bundle inline so the operator
can see the equity-curve chart + summary without leaving the
dashboard.

Routes:
  GET /reports/{job_id}                   -> view page (embeds equity-curve.html)
  GET /reports/{job_id}/files/{file_name} -> raw file (whitelist-only)
  GET /reports/compare?a=<job_a>&b=<job_b> -> side-by-side compare view
                                              (C13 — gap-analysis Part C)

The job_queue worker writes every backtest's bundle to
``var/reports/<job_id>/`` so this view just streams from disk.
Path traversal is blocked by a whitelist of the documented 5
file names.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim
from trading_system.webapp.fragments import fragment_context


router = APIRouter(prefix="/reports")


_REPORT_ROOT = Path("var") / "reports"
_ALLOWED_FILES: frozenset[str] = frozenset(
    {
        "equity-curve.html",
        "equity-curve.png",
        "trades.csv",
        "summary.json",
        "manifest.json",
        # Phase-6 attribution side-file (not part of the
        # REQ_F_RPT_001 5-file bundle; produced by the webapp's
        # job worker on top of write_report).
        "attribution.json",
    }
)
_MIME_BY_EXT = {
    ".html": "text/html; charset=utf-8",
    ".png": "image/png",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json",
}


def _require_auth(request: Request) -> None:
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verify_any_valid_claim(verifier, token)
    ):
        # The reports panel is a browser-friendly entry point; mirror
        # the dashboard's "soft redirect to /login" rather than a raw
        # 401 JSON body.
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )


def _report_dir(job_id: str) -> Path:
    """Resolve the bundle directory for ``job_id``. Defensive
    against path-traversal — the job_id is treated as an opaque
    leaf name (no path separators)."""
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webapp:reports:bad_job_id",
        )
    return _REPORT_ROOT / job_id


def _load_summary(dir_path: Path) -> dict | None:
    """Best-effort summary.json load. Returns ``None`` when the
    file is absent or unparseable — the comparison view falls
    back to ``"—"`` placeholders for missing fields."""
    p = dir_path / "summary.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@router.get("/compare", response_class=HTMLResponse, name="reports-compare")
def get_reports_compare(request: Request, a: str = "", b: str = ""):
    """C13 — side-by-side comparison of two backtest runs.

    Query params:
      ``?a=<job_a>&b=<job_b>``  — required; both job_ids must
      resolve to bundles on disk.

    Renders both ``equity-curve.html`` files in side-by-side
    iframes + a KPI comparison table reading
    ``summary.json`` from each bundle.
    """
    try:
        _require_auth(request)
    except HTTPException as e:
        if e.status_code == status.HTTP_303_SEE_OTHER:
            return RedirectResponse(url="/login", status_code=303)
        raise
    a = a.strip()
    b = b.strip()
    if not a or not b:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webapp:reports:compare:missing_query_param",
        )
    dir_a = _report_dir(a)
    dir_b = _report_dir(b)
    missing: list[str] = []
    if not dir_a.is_dir():
        missing.append(a)
    if not dir_b.is_dir():
        missing.append(b)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"webapp:reports:compare:not_found:{','.join(missing)}",
        )
    summary_a = _load_summary(dir_a)
    summary_b = _load_summary(dir_b)
    has_html_a = (dir_a / "equity-curve.html").is_file()
    has_html_b = (dir_b / "equity-curve.html").is_file()
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise RuntimeError("webapp:templates_missing")
    return templates.TemplateResponse(
        request=request,
        name="reports_compare.html",
        context={
            "job_a": a,
            "job_b": b,
            "summary_a": summary_a,
            "summary_b": summary_b,
            "has_html_a": has_html_a,
            "has_html_b": has_html_b,
            **fragment_context(request),
        },
    )


@router.get("/{job_id}", response_class=HTMLResponse, name="reports-view")
def get_report_view(job_id: str, request: Request):
    """View page — embeds the equity-curve HTML in an iframe +
    lists download links for the other artefacts."""
    try:
        _require_auth(request)
    except HTTPException as e:
        if e.status_code == status.HTTP_303_SEE_OTHER:
            return RedirectResponse(url="/login", status_code=303)
        raise
    dir_path = _report_dir(job_id)
    if not dir_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"webapp:reports:not_found:{job_id}",
        )
    available = sorted(
        f for f in _ALLOWED_FILES if (dir_path / f).is_file()
    )
    # Best-effort attribution load — render the panel only when
    # the side-file exists.
    import json

    attribution = None
    if "attribution.json" in available:
        try:
            attribution = json.loads(
                (dir_path / "attribution.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            attribution = None
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise RuntimeError("webapp:templates_missing")
    return templates.TemplateResponse(
        request=request,
        name="report_view.html",
        context={
            "job_id": job_id,
            "files": available,
            "has_html": "equity-curve.html" in available,
            "attribution": attribution,
            **fragment_context(request),
        },
    )


@router.get(
    "/{job_id}/files/{file_name}",
    name="reports-file",
)
def get_report_file(job_id: str, file_name: str, request: Request):
    """Serve a single artefact from the bundle. Whitelist-only."""
    _require_auth(request)
    if file_name not in _ALLOWED_FILES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"webapp:reports:file_unknown:{file_name}",
        )
    dir_path = _report_dir(job_id)
    file_path = dir_path / file_name
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"webapp:reports:file_missing:{job_id}/{file_name}",
        )
    media_type = _MIME_BY_EXT.get(file_path.suffix, "application/octet-stream")
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=file_name,
    )
