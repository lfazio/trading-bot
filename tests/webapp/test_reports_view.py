"""Tests for the CR-019 reports view (REQ_F_WEB2_005)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app


_SECRET = b"reports-test-secret"


def _make_client():
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    state = WebappState(token_verifier=verifier)
    return TestClient(create_app(state)), verifier


def _seed_report(tmp_path: Path, job_id: str) -> Path:
    """Create a fake report bundle on disk under
    ``var/reports/<job_id>/``. The reports view reads from the
    process's cwd-relative ``var/reports`` path, so we chdir tests
    that need filesystem isolation."""
    report_dir = tmp_path / "var" / "reports" / job_id
    report_dir.mkdir(parents=True)
    (report_dir / "equity-curve.html").write_text("<html>chart</html>", encoding="utf-8")
    (report_dir / "equity-curve.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (report_dir / "trades.csv").write_text("col\nrow\n", encoding="utf-8")
    (report_dir / "summary.json").write_text("{}", encoding="utf-8")
    (report_dir / "manifest.json").write_text(
        '{"config_hash":"abc","seed":"0","report_schema_version":"1"}',
        encoding="utf-8",
    )
    return report_dir


def _household_token(verifier):
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC))


def test_reports_view_redirects_unauth_to_login(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_report(tmp_path, "job-001")
    client, _ = _make_client()
    response = client.get("/reports/job-001", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_reports_view_returns_404_for_missing_bundle(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    client, verifier = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/reports/does-not-exist",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_reports_view_renders_iframe_and_links(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_report(tmp_path, "job-001")
    client, verifier = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/reports/job-001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.text
    assert "/reports/job-001/files/equity-curve.html" in body
    assert "iframe" in body
    # All 5 files appear as download links.
    for name in (
        "equity-curve.html",
        "equity-curve.png",
        "trades.csv",
        "summary.json",
        "manifest.json",
    ):
        assert name in body


def test_reports_file_serves_html(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_report(tmp_path, "job-001")
    client, verifier = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/reports/job-001/files/equity-curve.html",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert b"<html>chart</html>" in response.content


def test_reports_file_rejects_unknown_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_report(tmp_path, "job-001")
    client, verifier = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/reports/job-001/files/passwd",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_reports_route_rejects_traversal_attempts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    client, verifier = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/reports/..%2F..%2Fetc/files/equity-curve.html",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400 or response.status_code == 404


def test_reports_file_requires_auth(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_report(tmp_path, "job-001")
    client, _ = _make_client()
    response = client.get(
        "/reports/job-001/files/equity-curve.html",
        follow_redirects=False,
    )
    # Auth fails with the configured "303 -> /login" redirect for
    # the view-tier surface.
    assert response.status_code in (303, 401)
