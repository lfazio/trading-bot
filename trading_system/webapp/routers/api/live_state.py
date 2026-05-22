"""``GET /api/accounts/{account_id}/live-state`` — port of the
stdlib webui's Phase-A read endpoint.

REQ refs:
- REQ_F_FAS_001 — every CR-004 endpoint exposed route-for-route.
- REQ_NF_FAS_001 / REQ_NF_WEB_002 — byte-identical replay holds.

The handler delegates to a ``LiveStateReader`` Protocol attached to
the FastAPI app's state at construction (``app.state.live_state_reader``).
This keeps the webapp free of imports on concrete runtime types
(REQ_SDD_FAS_001 import-graph audit).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Request, status
from starlette.responses import Response

from trading_system.models.identifiers import AccountId
from trading_system.webapp.auth_deps import RequestRequireAnyValidClaim
from trading_system.webapp.canonical import canonical_json_response
from trading_system.webui.schemas import LiveStateResponse


router = APIRouter(prefix="/api")


@runtime_checkable
class LiveStateReader(Protocol):
    """Same shape as the stdlib webui's ``LiveStateReader`` Protocol
    — keeping the two surfaces aligned means the operator can swap
    backends behind a single concrete reader."""

    def live_state(
        self, *, account_id: AccountId, as_of: datetime
    ) -> LiveStateResponse: ...


def _reader(request: Request) -> LiveStateReader:
    reader = getattr(request.app.state, "live_state_reader", None)
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:reader_missing",
        )
    return reader


@router.get(
    "/accounts/{account_id}/live-state",
    response_class=Response,
    summary="Read live state for an account",
    description=(
        "Returns the canonical live-state envelope for the named "
        "account. The body shape mirrors the stdlib webui's "
        "``LiveStateResponse`` byte-for-byte."
    ),
)
def get_live_state(
    account_id: AccountId,
    request: RequestRequireAnyValidClaim,
) -> Response:
    """REQ_F_FAS_001 — byte-identical canonical JSON."""
    reader = _reader(request)
    payload = reader.live_state(
        account_id=account_id,
        as_of=datetime.now(tz=UTC),
    )
    return canonical_json_response(payload)
