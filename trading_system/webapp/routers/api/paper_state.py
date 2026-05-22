"""GET /api/accounts/{account_id}/paper-state — REQ_F_WEB2_003.

Request-response companion to the SSE channel at
``/events/paper-state``. Returns the same ``PaperStateResponse``
shape so the dashboard panel can issue one initial fetch + then
upgrade to the SSE stream.

REQ refs:
- REQ_F_WEB2_003 — paper-trading dashboard panel state surface.
- REQ_NF_WEB2_001 — canonical-JSON body; byte-identical replay.
- REQ_SDD_FAS_001 — the router consumes a Protocol-shaped reader
  off ``app.state``, never reaches the concrete runtime / registry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Request, Response, status

from trading_system.models.identifiers import AccountId
from trading_system.webapp.auth_deps import RequestRequireAnyValidClaim
from trading_system.webapp.canonical import canonical_json_response
from trading_system.webui.schemas import PaperStateResponse


router = APIRouter(prefix="/api/accounts")


@runtime_checkable
class PaperStateReader(Protocol):
    """Protocol surface — see ``paper_state_reader.RuntimePaperStateReader``
    for the concrete implementation."""

    def paper_state(
        self, *, account_id: AccountId, as_of: datetime
    ) -> PaperStateResponse: ...


def _reader(request: Request) -> PaperStateReader:
    reader = getattr(request.app.state, "paper_state_reader", None)
    if reader is None or not hasattr(reader, "paper_state"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:paper_state_reader_missing",
        )
    return reader


@router.get(
    "/{account_id}/paper-state",
    summary="Paper-trading session state",
    description=(
        "Read endpoint for the paper-trading dashboard panel. "
        "Returns a ``PaperStateResponse`` snapshot. When the "
        "session is not currently registered (never started, or "
        "already stopped), the response is the documented "
        "all-zeroed shape with ``is_alive=false`` — keeps the "
        "client-side rendering single-shape."
    ),
)
async def get_paper_state(
    account_id: str,
    request: RequestRequireAnyValidClaim,
) -> Response:
    """REQ_F_WEB2_003 — canonical-JSON paper-state body."""
    reader = _reader(request)
    snapshot = reader.paper_state(
        account_id=AccountId(account_id),
        as_of=datetime.now(tz=UTC),
    )
    return canonical_json_response(snapshot)
