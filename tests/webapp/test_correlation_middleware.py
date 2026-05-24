"""TC_FAS_C2_001 — correlation-id propagation through the webapp
request boundary (Phase-8 hardening C2 / REQ_SDS_CRS_001).

The middleware MUST:
1. Generate a fresh ``X-Request-ID`` when the client doesn't supply one.
2. Echo the client-supplied ``X-Request-ID`` back on the response.
3. Extract ``account_id`` from path segments (``/api/accounts/<aid>/...``
   and ``/paper-sessions/<aid>/...``).
4. Bind ``LogContext`` so handlers calling
   ``trading_system.observability.structured_log`` carry the correlation
   id automatically.
"""

from __future__ import annotations

import io
import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_system.observability import (
    configure_logging,
    current_context,
    structured_log,
)
from trading_system.webapp.middleware import (
    CorrelationMiddleware,
    _extract_account_id_from_path,
)


def _client_with_probe() -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.add_middleware(CorrelationMiddleware)

    @app.get("/api/accounts/{aid}/probe")
    def probe(aid: str) -> dict:
        ctx = current_context()
        return {
            "account_id": ctx.account_id if ctx is not None else None,
            "corr_id": ctx.corr_id if ctx is not None else None,
        }

    @app.get("/")
    def root() -> dict:
        ctx = current_context()
        return {
            "account_id": ctx.account_id if ctx is not None else None,
            "corr_id": ctx.corr_id if ctx is not None else None,
        }

    @app.get("/paper-sessions/{aid}/sse")
    def paper_sse(aid: str) -> dict:
        ctx = current_context()
        return {
            "account_id": ctx.account_id if ctx is not None else None,
        }

    return TestClient(app), app


# ---------------------------------------------------------------------------
# Header round-trip
# ---------------------------------------------------------------------------


def test_request_id_generated_when_client_omits_it() -> None:
    client, _ = _client_with_probe()
    response = client.get("/")
    assert response.status_code == 200
    rid = response.headers.get("X-Request-ID")
    assert rid is not None and len(rid) == 32  # uuid4().hex


def test_client_supplied_request_id_is_echoed_unchanged() -> None:
    client, _ = _client_with_probe()
    response = client.get("/", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["X-Request-ID"] == "trace-abc-123"


# ---------------------------------------------------------------------------
# Account-id extraction
# ---------------------------------------------------------------------------


def test_account_id_extracted_from_api_accounts_path() -> None:
    assert _extract_account_id_from_path("/api/accounts/h-42/live-state") == "h-42"


def test_account_id_extracted_from_paper_sessions_path() -> None:
    assert _extract_account_id_from_path("/paper-sessions/h-42/sse") == "h-42"


def test_account_id_defaults_when_path_has_no_scope() -> None:
    assert _extract_account_id_from_path("/") == "default"
    assert _extract_account_id_from_path("/health") == "default"
    assert _extract_account_id_from_path("/api") == "default"


def test_account_id_binding_visible_inside_handler() -> None:
    client, _ = _client_with_probe()
    response = client.get("/api/accounts/operator-1/probe")
    assert response.status_code == 200
    payload = response.json()
    assert payload["account_id"] == "operator-1"
    assert payload["corr_id"] is not None


def test_correlation_id_visible_inside_handler() -> None:
    client, _ = _client_with_probe()
    response = client.get("/", headers={"X-Request-ID": "trace-xyz"})
    payload = response.json()
    assert payload["corr_id"] == "trace-xyz"
    assert payload["account_id"] == "default"


# ---------------------------------------------------------------------------
# Structured-log integration
# ---------------------------------------------------------------------------


def test_structured_log_inside_handler_carries_corr_id() -> None:
    """End-to-end: a handler calling ``structured_log`` SHALL emit a
    JSON line tagged with the request's corr_id + account_id."""
    sink = io.StringIO()
    configure_logging(level="INFO", json_output=True, stream=sink)
    logger = logging.getLogger("test.correlation")
    app = FastAPI()
    app.add_middleware(CorrelationMiddleware)

    @app.get("/api/accounts/{aid}/log")
    def emit(aid: str) -> dict:
        structured_log(logger, logging.INFO, "test", "handler-emitted", aid=aid)
        return {"ok": True}

    client = TestClient(app)
    client.get(
        "/api/accounts/h-7/log",
        headers={"X-Request-ID": "trace-end-to-end"},
    )

    output = sink.getvalue().strip().splitlines()
    matches = [
        ln for ln in output if "handler-emitted" in ln
    ]
    assert matches, f"no structured log line found in: {output!r}"
    line = json.loads(matches[0])
    assert line["corr_id"] == "trace-end-to-end"
    assert line["account_id"] == "h-7"
    assert line["category"] == "test"


def test_context_does_not_leak_between_requests() -> None:
    """LogContext SHALL NOT bleed across request boundaries — each
    new request gets a fresh corr_id."""
    client, _ = _client_with_probe()
    response_a = client.get("/", headers={"X-Request-ID": "trace-A"})
    response_b = client.get("/", headers={"X-Request-ID": "trace-B"})
    assert response_a.json()["corr_id"] == "trace-A"
    assert response_b.json()["corr_id"] == "trace-B"
    # And after both calls return, the ambient context SHALL be unset.
    assert current_context() is None
