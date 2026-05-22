"""Reports panel — REQ_F_WEB2_005.

Surfaces the CR-016 5-file report bundle inline so the operator
can see the equity-curve chart + summary without leaving the
dashboard.

Routes:
  GET /reports/{job_id}                   -> view page (embeds equity-curve.html)
  GET /reports/{job_id}/files/{file_name} -> raw file (whitelist-only)

The job_queue worker writes every backtest's bundle to
``var/reports/<job_id>/`` so this view just streams from disk.
Path traversal is blocked by a whitelist of the documented 5
file names.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim


router = APIRouter(prefix="/reports")


_REPORT_ROOT = Path("var") / "reports"
_ALLOWED_FILES: frozenset[str] = frozenset(
    {
        "equity-curve.html",
        "equity-curve.png",
        "trades.csv",
        "summary.json",
        "manifest.json",
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
