"""Server-Sent Events live-state push channel (REQ_F_FAS_003).

REQ refs:
- REQ_F_FAS_003 — SSE channel at ``GET /events/live-state``;
  ``text/event-stream`` media type; event ``id`` is the monotonic
  ``as_of`` ISO-8601 timestamp so HTMX `hx-sse` can resume after
  disconnect.
- REQ_NF_FAS_001 — every event's data payload is the canonical-JSON
  form of the same shape as ``GET /api/accounts/{aid}/live-state``;
  two consecutive subscribers see byte-identical payloads for the
  same snapshot.
- REQ_SDS_FAS_003 — SSE chosen over WebSocket because the dashboard
  push channel is unidirectional.

The router pulls a ``LiveStateReader`` off ``app.state`` and asks
it for an async stream of snapshots. The Phase-A reader's
``subscribe`` yields one snapshot per tick (default 5s); a runtime
``LiveStateReader`` wired in Phase-B production replaces this with
the actual engine's tick stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from trading_system.models.identifiers import AccountId
from trading_system.notifications.canonical import canonical_json_line
from trading_system.webapp.auth_deps import RequestRequireAnyValidClaim
from trading_system.webui.schemas import LiveStateResponse, PaperStateResponse


router = APIRouter(prefix="/events")


@runtime_checkable
class LiveStateStreamReader(Protocol):
    """Streaming surface — the Phase-A reader implements both this
    and the request-response ``LiveStateReader`` so wiring stays
    minimal. The streaming method returns an async iterator so the
    runtime can hook into the engine's tick boundary without
    polling."""

    def subscribe(
        self, *, account_id: AccountId
    ) -> AsyncIterator[LiveStateResponse]: ...


def _stream_reader(request: Request) -> LiveStateStreamReader:
    reader = getattr(request.app.state, "live_state_reader", None)
    if reader is None or not hasattr(reader, "subscribe"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:reader_stream_missing",
        )
    return reader


@router.get(
    "/live-state",
    summary="SSE live-state stream",
    description=(
        "Server-Sent Events channel that pushes the live-state "
        "envelope to subscribed clients. Each event's ``id`` is "
        "the snapshot's ``as_of`` ISO-8601 timestamp so HTMX "
        "`hx-sse` resumes the stream after disconnect."
    ),
)
async def stream_live_state(request: RequestRequireAnyValidClaim) -> EventSourceResponse:
    """REQ_F_FAS_003 — emit one ``live-state`` event per reader tick."""
    reader = _stream_reader(request)
    account_id = AccountId(
        request.query_params.get("account_id", "default").strip() or "default"
    )

    async def event_generator() -> AsyncIterator[dict]:
        async for snapshot in reader.subscribe(account_id=account_id):
            if await request.is_disconnected():
                return
            yield {
                "id": snapshot.as_of.isoformat(),
                "event": "live-state",
                "data": canonical_json_line(snapshot),
            }

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Phase-A demo subscribe helper — exported so `default_app()`'s demo
# reader can reuse it. Production readers replace this with the
# engine's tick stream.
# ---------------------------------------------------------------------------


@runtime_checkable
class PaperStateStreamReader(Protocol):
    """Streaming surface for the paper-trading panel
    (REQ_F_WEB2_003). The concrete
    ``RuntimePaperStateReader`` implements both this and the
    request-response ``paper_state`` shape."""

    def subscribe(
        self, *, account_id: AccountId
    ) -> AsyncIterator[PaperStateResponse]: ...


def _paper_stream_reader(request: Request) -> PaperStateStreamReader:
    reader = getattr(request.app.state, "paper_state_reader", None)
    if reader is None or not hasattr(reader, "subscribe"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:paper_state_stream_missing",
        )
    return reader


@router.get(
    "/paper-state",
    summary="SSE paper-trading state stream",
    description=(
        "Server-Sent Events channel that pushes the paper-trading "
        "session's state envelope to subscribed clients. Each "
        "event's ``id`` is the snapshot's ``as_of`` ISO-8601 "
        "timestamp so HTMX `hx-sse` resumes the stream after "
        "disconnect. Cadence is set by the wired reader's "
        "``tick_seconds`` (default 2s for paper-state vs. 5s for "
        "the aggregate live-state — the paper panel needs to feel "
        "live)."
    ),
)
async def stream_paper_state(
    request: RequestRequireAnyValidClaim,
) -> EventSourceResponse:
    """REQ_F_WEB2_003 — emit one ``paper-state`` event per reader tick."""
    reader = _paper_stream_reader(request)
    account_id = AccountId(
        request.query_params.get("account_id", "").strip() or "default"
    )
    # CR-026 / REQ_SDD_PAP_010 — optional pin overrides which
    # symbol the reader marks as ``pinned_symbol`` on every snapshot.
    pinned_symbol = request.query_params.get("pin", "").strip() or None

    async def event_generator() -> AsyncIterator[dict]:
        async for snapshot in reader.subscribe(
            account_id=account_id, pinned_symbol=pinned_symbol
        ):
            if await request.is_disconnected():
                return
            yield {
                "id": snapshot.as_of.isoformat(),
                "event": "paper-state",
                "data": canonical_json_line(snapshot),
            }

    return EventSourceResponse(event_generator())


async def demo_subscribe(
    snapshot_factory,  # type: ignore[no-untyped-def]
    *,
    account_id: AccountId,
    tick_seconds: float = 5.0,
) -> AsyncIterator[LiveStateResponse]:
    """Emits a snapshot every ``tick_seconds`` seconds.

    ``snapshot_factory`` is a callable ``(account_id, as_of) ->
    LiveStateResponse`` — the Phase-A demo reader passes its
    existing static-builder; production readers ignore this helper
    and wire the engine's tick stream directly.
    """
    while True:
        yield snapshot_factory(account_id=account_id, as_of=datetime.now(UTC))
        await asyncio.sleep(tick_seconds)
