"""GET /api/inbox — notification ring buffer (REQ_F_WEB2_009)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from trading_system.webapp.auth_deps import RequestRequireAnyValidClaim
from trading_system.webapp.canonical import canonical_json_response
from trading_system.webapp.inbox import InboxChannel


router = APIRouter(prefix="/api")


def _channel(request: Request) -> InboxChannel:
    channel = getattr(request.app.state, "notification_inbox", None)
    if not isinstance(channel, InboxChannel):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webapp:inbox_channel_missing",
        )
    return channel


@router.get(
    "/inbox",
    summary="Notifications inbox snapshot",
    description=(
        "Returns the bounded ring buffer of recent operator-facing "
        "notifications, newest LAST. The body is canonical-JSON; "
        "equal buffer states produce byte-identical bodies."
    ),
)
def get_inbox(request: RequestRequireAnyValidClaim) -> Response:
    """REQ_F_WEB2_009 — canonical-JSON inbox snapshot."""
    channel = _channel(request)
    return canonical_json_response(
        {"entries": list(channel.snapshot())},
    )
