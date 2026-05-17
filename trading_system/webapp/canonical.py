"""Canonical-JSON response helper for the FastAPI surface.

REQ refs:
- REQ_NF_FAS_001 / REQ_NF_WEB_002 — byte-identical replay holds on
  identical ``(account_id, as_of)`` tuples.
- REQ_SDD_FAS_002 — bypass FastAPI's auto-serialisation; emit the
  canonical body directly via ``starlette.responses.Response``.

We deliberately reuse ``notifications.canonical.canonical_json_line``
so the FastAPI surface emits **byte-identical bytes** to the stdlib
``webui/`` path. Routing serialisation through Pydantic's
``model_dump_json`` is permissible but would couple replay
determinism to pydantic's encoder version; using the project-wide
canonical serialiser is simpler and provably stable.
"""

from __future__ import annotations

from starlette.responses import Response

from trading_system.notifications.canonical import canonical_json_line


def canonical_json_response(payload: object, *, status_code: int = 200) -> Response:
    """Return a ``Response`` whose body is the canonical-JSON form of
    ``payload``. ``payload`` may be any type
    ``canonical_json_line`` knows how to coerce (frozen dataclasses,
    dicts, primitive values).
    """
    body = canonical_json_line(payload).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json",
        status_code=status_code,
    )


def canonical_error_response(reason: str, *, status_code: int) -> Response:
    """Wrap a categorised Err string into the canonical
    ``{"error": <reason>}`` shape."""
    return canonical_json_response({"error": reason}, status_code=status_code)
