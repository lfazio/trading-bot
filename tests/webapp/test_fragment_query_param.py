"""REQ_SDS_WEB2_002 + REQ_SDD_WEB2_001 — fragment query param.

Every view route SHALL accept ``?fragment=<truthy>`` (or the
``HX-Request: true`` header) and, when present, render only the
inner ``{% block content %}`` markup — no <html>, no <head>,
no <body>, no global <script>.

The audit is structural + integration: for each documented view
route, fire a GET with ``?fragment=1`` + a valid token + verify
the response carries the inner content but NOT the chrome.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app
from trading_system.webapp.fragments import fragment_context, is_fragment_request
from trading_system.webapp.inbox import InboxChannel
from trading_system.webapp.job_queue import InProcessJobQueue
from trading_system.webapp.runtimes.paper_trading import RuntimeRegistry


_SECRET = b"fragment-test-secret"


def _client():
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    state = WebappState(
        token_verifier=verifier,
        runtime_registry=RuntimeRegistry(),
        notification_inbox=InboxChannel(),
        job_queue=InProcessJobQueue(workers=1),
    )
    return TestClient(create_app(state)), verifier


def _token(verifier):
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC))


# ---------------------------------------------------------------------------
# is_fragment_request / fragment_context unit tests
# ---------------------------------------------------------------------------


def test_fragment_query_truthy_values_detected() -> None:
    """``?fragment=1`` / ``true`` / ``yes`` / ``on`` SHALL all
    flip the fragment flag (case-insensitive)."""
    from fastapi import Request
    from starlette.types import Scope

    def _make(query: bytes, headers: list | None = None) -> Request:
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": query,
            "headers": headers or [],
        }
        return Request(scope=scope)

    assert is_fragment_request(_make(b"fragment=1")) is True
    assert is_fragment_request(_make(b"fragment=true")) is True
    assert is_fragment_request(_make(b"fragment=TRUE")) is True
    assert is_fragment_request(_make(b"fragment=yes")) is True
    assert is_fragment_request(_make(b"fragment=on")) is True
    # Falsy / absent values stay False.
    assert is_fragment_request(_make(b"fragment=0")) is False
    assert is_fragment_request(_make(b"fragment=")) is False
    assert is_fragment_request(_make(b"")) is False
    # HX-Request header also counts.
    assert (
        is_fragment_request(
            _make(b"", headers=[(b"hx-request", b"true")])
        )
        is True
    )


def test_fragment_context_returns_dynamic_parent_template() -> None:
    """``fragment_context`` SHALL emit ``parent_template`` that
    points at the fragment base when the request asks for it,
    and at the full base otherwise."""
    from fastapi import Request

    def _make(qs: bytes) -> Request:
        return Request(
            scope={
                "type": "http",
                "method": "GET",
                "path": "/",
                "query_string": qs,
                "headers": [],
            }
        )

    assert fragment_context(_make(b"")) == {"parent_template": "base.html"}
    assert fragment_context(_make(b"fragment=1")) == {
        "parent_template": "fragment_base.html"
    }


# ---------------------------------------------------------------------------
# Integration — every documented view honours ?fragment=1
# ---------------------------------------------------------------------------


# Routes that render an HTML page extending base.html. Excludes
# api/ routes (they emit canonical JSON, not Jinja templates).
_PARAMETRISED_ROUTES = (
    "/",
    "/login",
    "/jobs",
    "/notifications",
    "/onboarding",
    "/operator/recovery",
    "/strategies",
)


def _all_fragment_chrome_absent(body: str) -> bool:
    """The fragment base renders ONLY the inner content. The
    body SHALL NOT carry the documented chrome markers."""
    if "<!doctype html>" in body.lower():
        return False
    if re.search(r"<html\b", body, re.IGNORECASE):
        return False
    if re.search(r"<header[^>]*\bclass=[\"']topbar", body, re.IGNORECASE):
        return False
    # No global script tag with src to htmx.min.js (that's the
    # chrome's <script> include).
    if 'src="/static/htmx.min.js"' in body or "static/htmx.min.js" in body:
        return False
    return True


def _has_chrome(body: str) -> bool:
    """Chrome = full HTML document (login deliberately hides the
    topbar via show_chrome=False so we only require the <html>
    + <body> shell)."""
    return (
        "<!doctype html>" in body.lower()
        and re.search(r"<html\b", body, re.IGNORECASE) is not None
    )


def test_every_view_renders_fragment_when_asked() -> None:
    """REQ_SDD_WEB2_001 — every view route SHALL honour
    ``?fragment=1`` + emit chrome-less markup."""
    client, verifier = _client()
    token = _token(verifier)
    for route in _PARAMETRISED_ROUTES:
        sep = "&" if "?" in route else "?"
        url = f"{route}{sep}fragment=1"
        response = client.get(
            url, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, (
            f"{route}: expected 200, got {response.status_code} (body: {response.text[:200]})"
        )
        assert _all_fragment_chrome_absent(response.text), (
            f"{route} rendered chrome when fragment=1 was requested. "
            f"First 400 chars: {response.text[:400]!r}"
        )


def test_every_view_renders_full_chrome_by_default() -> None:
    """Default (no fragment param) SHALL render the full base
    chrome — chrome markers SHALL be present."""
    client, verifier = _client()
    token = _token(verifier)
    for route in _PARAMETRISED_ROUTES:
        response = client.get(
            route, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, (
            f"{route}: expected 200, got {response.status_code}"
        )
        assert _has_chrome(response.text), (
            f"{route} dropped chrome when fragment was NOT requested. "
            f"First 400 chars: {response.text[:400]!r}"
        )


def test_hx_request_header_alone_triggers_fragment_render() -> None:
    """An HTMX-issued GET with ``HX-Request: true`` SHALL receive
    the fragment automatically, even when the URL omits the query
    param."""
    client, verifier = _client()
    token = _token(verifier)
    response = client.get(
        "/",
        headers={
            "Authorization": f"Bearer {token}",
            "HX-Request": "true",
        },
    )
    assert response.status_code == 200
    assert _all_fragment_chrome_absent(response.text)


# ---------------------------------------------------------------------------
# Structural audit — every view template extends parent_template
# ---------------------------------------------------------------------------


def test_every_view_template_extends_dynamic_parent_template() -> None:
    """REQ_SDD_WEB2_001 — every top-level view template SHALL
    use the ``parent_template|default("base.html")`` extends
    pattern so the fragment switch flips at render time."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    templates_dir = repo_root / "trading_system" / "webapp" / "templates"
    skip = {"base.html", "fragment_base.html", "partials"}
    for path in templates_dir.iterdir():
        if path.is_dir() or path.name in skip:
            continue
        if path.suffix != ".html":
            continue
        body = path.read_text(encoding="utf-8")
        assert (
            'extends parent_template|default("base.html")' in body
        ), (
            f"{path.name} doesn't use the dynamic extends pattern — "
            f"REQ_SDD_WEB2_001 requires it so ?fragment=1 strips chrome."
        )
