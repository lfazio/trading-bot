"""Integration tests for ``Router`` dispatch + ``WebUIServer``
end-to-end HTTP behaviour.

REQ refs: REQ_F_WEB_001 (stdlib HTTP API), REQ_SDS_WEB_001
(stdlib-based server in a child / thread so HTTP crash never
propagates), REQ_SDD_WEB_001 (http.server.ThreadingHTTPServer
backend)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from trading_system.webui.schemas import JsonResponse
from trading_system.webui.server import (
    Request,
    Route,
    Router,
    WebUIServer,
)


# ---------------------------------------------------------------------------
# Request invariants
# ---------------------------------------------------------------------------


def test_request_rejects_empty_method() -> None:
    with pytest.raises(ValueError, match="method"):
        Request(method="", path="/", headers={})


def test_request_rejects_relative_path() -> None:
    with pytest.raises(ValueError, match="path"):
        Request(method="GET", path="relative", headers={})


def test_request_json_parses_dict() -> None:
    r = Request(
        method="POST", path="/", headers={}, body=b'{"a":1}'
    )
    assert r.json() == {"a": 1}


def test_request_json_empty_body_returns_empty_dict() -> None:
    r = Request(method="GET", path="/", headers={})
    assert r.json() == {}


def test_request_json_malformed_returns_empty_dict() -> None:
    r = Request(method="POST", path="/", headers={}, body=b"<not-json")
    assert r.json() == {}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def test_router_dispatches_exact_match() -> None:
    router = Router()
    router.register(
        Route(method="GET", path="/ping", handler=lambda _r: JsonResponse(200, "pong"))
    )
    resp = router.dispatch(Request(method="GET", path="/ping", headers={}))
    assert resp.body == "pong"


def test_router_returns_404_for_unmatched() -> None:
    router = Router()
    resp = router.dispatch(Request(method="GET", path="/ghost", headers={}))
    assert resp.status_code == 404
    assert "webui:route_not_found" in json.loads(resp.body)["error"]


def test_router_404_distinguishes_method_mismatch() -> None:
    router = Router()
    router.register(
        Route(method="GET", path="/x", handler=lambda _r: JsonResponse(200, "{}"))
    )
    resp = router.dispatch(Request(method="POST", path="/x", headers={}))
    assert resp.status_code == 404


def test_router_rejects_duplicate_registration() -> None:
    router = Router()
    router.register(
        Route(method="GET", path="/x", handler=lambda _r: JsonResponse(200, "{}"))
    )
    with pytest.raises(ValueError, match="duplicate"):
        router.register(
            Route(method="GET", path="/x", handler=lambda _r: JsonResponse(200, "{}"))
        )


def test_route_rejects_relative_path() -> None:
    with pytest.raises(ValueError, match="path"):
        Route(method="GET", path="relative", handler=lambda _r: JsonResponse(200, "{}"))


# ---------------------------------------------------------------------------
# End-to-end — start a real HTTP server on an ephemeral port
# ---------------------------------------------------------------------------


def _start_server(router: Router) -> WebUIServer:
    srv = WebUIServer(router=router, host="127.0.0.1", port=0)
    srv.start()
    return srv


def test_end_to_end_get() -> None:
    router = Router()
    router.register(
        Route(
            method="GET",
            path="/ping",
            handler=lambda _r: JsonResponse.from_canonical({"ok": True}),
        )
    )
    srv = _start_server(router)
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{srv.port}/ping", timeout=2
        ) as resp:
            assert resp.status == 200
            obj = json.loads(resp.read().decode("utf-8"))
            assert obj == {"ok": True}
    finally:
        srv.stop()


def test_end_to_end_post() -> None:
    received: list[bytes] = []

    def echo(request: Request) -> JsonResponse:
        received.append(request.body)
        return JsonResponse.from_canonical({"received_len": len(request.body)})

    router = Router()
    router.register(Route(method="POST", path="/echo", handler=echo))
    srv = _start_server(router)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/echo",
            data=b'{"payload":"x"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            assert resp.status == 200
            obj = json.loads(resp.read().decode("utf-8"))
            assert obj["received_len"] == len(b'{"payload":"x"}')
        assert received == [b'{"payload":"x"}']
    finally:
        srv.stop()


def test_end_to_end_404_propagates_status() -> None:
    router = Router()
    srv = _start_server(router)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(
                f"http://127.0.0.1:{srv.port}/ghost", timeout=2
            )
        assert exc_info.value.code == 404
    finally:
        srv.stop()


def test_server_double_start_rejected() -> None:
    srv = _start_server(Router())
    try:
        with pytest.raises(RuntimeError, match="already started"):
            srv.start()
    finally:
        srv.stop()


def test_server_stop_is_idempotent() -> None:
    srv = WebUIServer(router=Router())
    # Never started — stop is a no-op.
    srv.stop()
    srv.stop()
