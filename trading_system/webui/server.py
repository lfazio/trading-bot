"""Stdlib HTTP server skeleton — REQ_F_WEB_001 / REQ_SDS_WEB_001.

Phase A ships a synchronous ``http.server.ThreadingHTTPServer``-based
implementation. The trading process spawns the server in a separate
thread / process so an HTTP crash never propagates to the trading
critical path (REQ_NF_WEB_001); Phase A wires the thread path —
child-process isolation is a Phase-B follow-up.

The ``Router`` maps ``(method, path)`` tuples to handler callables
that take a ``Request`` and return a ``JsonResponse``. Path matching
is exact in v1; Phase B adds path templating
(``/accounts/{account_id}/positions``).

Routes never block the trading process — handlers should be fast
(< 100ms typical) or push the work to a background queue (the
Phase-B ``JobQueue``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from trading_system.webui.schemas import JsonResponse


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Request:
    """Lightweight request shape passed to handlers.

    Sealed so handler tests can construct fixtures without touching
    the http.server BaseHTTPRequestHandler internals.
    """

    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes = b""

    def __post_init__(self) -> None:
        if not self.method.strip():
            raise ValueError("Request.method must be non-empty")
        if not self.path.startswith("/"):
            raise ValueError(
                f"Request.path must start with '/', got {self.path!r}"
            )

    def json(self) -> dict[str, Any]:
        """Parse the body as JSON. Returns an empty dict for an
        empty body so handlers can use ``request.json().get(key)``
        without a separate None check."""
        if not self.body:
            return {}
        try:
            decoded = json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if isinstance(decoded, dict):
            return decoded
        return {}


Handler = Callable[[Request], JsonResponse]


@dataclass(frozen=True, slots=True)
class Route:
    """One route registration."""

    method: str
    path: str
    handler: Handler

    def __post_init__(self) -> None:
        if not self.method.strip():
            raise ValueError("Route.method must be non-empty")
        if not self.path.startswith("/"):
            raise ValueError(
                f"Route.path must start with '/', got {self.path!r}"
            )


@dataclass(slots=True)
class Router:
    """Exact (method, path) → handler matcher.

    Phase A is sufficient for the single mutation endpoint + a
    handful of read endpoints; Phase B upgrades to path templating
    + per-route auth scope declarations.
    """

    routes: list[Route] = field(default_factory=list)

    def register(self, route: Route) -> None:
        # Detect duplicate registrations — they're a programmer
        # error rather than a runtime fallthrough.
        for existing in self.routes:
            if existing.method == route.method and existing.path == route.path:
                raise ValueError(
                    f"Router: duplicate route registration for "
                    f"{route.method} {route.path}"
                )
        self.routes.append(route)

    def dispatch(self, request: Request) -> JsonResponse:
        for route in self.routes:
            if route.method == request.method and route.path == request.path:
                return route.handler(request)
        return JsonResponse.error(
            404, f"webui:route_not_found:{request.method}:{request.path}"
        )


# ---------------------------------------------------------------------------
# stdlib HTTP server adapter
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Translates http.server callbacks into Router dispatch."""

    router: Router  # injected via the WebUIServer factory

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Stdlib BaseHTTPRequestHandler logs to stderr by default;
        # route everything through the structured logger instead so
        # JSON-line output stays consistent with the rest of the
        # process (REQ_NF_LOG_001 family).
        _LOG.info(
            "webui access",
            extra={
                "category": "system",
                "payload": {
                    "client": self.address_string(),
                    "message": format % args,
                },
            },
        )

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _serve(self, method: str) -> None:
        request = Request(
            method=method,
            path=self.path,
            headers={k: v for k, v in self.headers.items()},
            body=self._read_body() if method in {"POST", "PUT", "PATCH"} else b"",
        )
        response = self.router.dispatch(request)
        body_bytes = response.body.encode("utf-8")
        self.send_response(response.status_code)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self) -> None:  # noqa: N802 — http.server interface
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._serve("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._serve("DELETE")


@dataclass(slots=True)
class WebUIServer:
    """Threaded stdlib HTTP server.

    Lifecycle:
        srv = WebUIServer(router=..., host="127.0.0.1", port=0)
        srv.start()            # spawns a background thread
        ...                    # test makes requests
        srv.stop()             # shuts the listener down

    ``port=0`` asks the kernel for an ephemeral port; ``srv.port``
    holds the resolved port after ``start()``.
    """

    router: Router
    host: str = "127.0.0.1"
    port: int = 0
    _server: ThreadingHTTPServer | None = None
    _thread: Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("WebUIServer already started")
        handler_class = type(
            "_BoundHandler",
            (_Handler,),
            {"router": self.router},
        )
        self._server = ThreadingHTTPServer((self.host, self.port), handler_class)
        self.port = self._server.server_address[1]
        self._thread = Thread(
            target=self._server.serve_forever, name="webui", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
