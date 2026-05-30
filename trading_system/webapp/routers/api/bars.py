"""CR-029 — `GET /api/accounts/{account_id}/bars` read endpoint.

Operator query: "what was BNP.PA doing between T0 and T1?" The
route returns the per-symbol bar window persisted by the CR-029
fan-out + the CR-021 yfinance cache.

REQ refs:
- REQ_F_PER_013 — endpoint shape + canonical-JSON.
- REQ_SDD_PER_013 — auth + query-param validation + Err codes.
- REQ_F_ACC_010 — per-account scoping; household-claim REJECTED.
- REQ_NF_TOK_001 — no raw token in any log.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Protocol, runtime_checkable

from fastapi import APIRouter, Request, status
from starlette.responses import Response

from trading_system.accounts.token_verifier import HOUSEHOLD_CLAIM
from trading_system.models.identifiers import AccountId, InstrumentId
from trading_system.observability import structured_log
from trading_system.result import Err
from trading_system.webapp.canonical import (
    canonical_error_response,
    canonical_json_response,
)


_AUDIT_LOGGER = logging.getLogger(__name__)


router = APIRouter(prefix="/api/accounts")


@runtime_checkable
class InstrumentBarReaderView(Protocol):
    """Subset of ``InstrumentBarRepository`` the route consumes."""

    def bars_for(
        self,
        *,
        account_id: AccountId,
        instrument_id: InstrumentId,
        start: datetime,
        end: datetime,
    ): ...


# ---------------------------------------------------------------------------
# Auth helpers (mirror api/live_mode.py + api/hypotheses.py)
# ---------------------------------------------------------------------------


_BEARER_PREFIX = "Bearer "


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX):].strip() or None
    if auth:
        return auth.strip() or None
    legacy = request.headers.get("x-operator-token", "").strip()
    return legacy or None


def _check_account_token(
    request: Request, *, account_id: AccountId
) -> tuple[str, str] | Response:
    bearer = _extract_bearer(request)
    if bearer is None:
        return canonical_error_response(
            "registry:token_invalid",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        return canonical_error_response(
            "webapp:token_verifier_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if verifier.verify(bearer, account_id=HOUSEHOLD_CLAIM):
        return canonical_error_response(
            "registry:household_claim_rejected",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not verifier.verify(bearer, account_id=str(account_id)):
        return canonical_error_response(
            "registry:token_invalid",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    token_hash = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
    return bearer, token_hash


def _bar_reader(request: Request) -> InstrumentBarReaderView | None:
    return getattr(request.app.state, "instrument_bar_repository", None)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get(
    "/{account_id}/bars",
    response_class=Response,
    summary="Per-symbol bar window (CR-029 / REQ_F_PER_013)",
)
def get_bars(
    account_id: AccountId,
    request: Request,
    instrument: str = "",
    start: str = "",
    end: str = "",
) -> Response:
    """REQ_F_PER_013 / REQ_SDD_PER_013 — operator-token-gated
    per-account-scoped read of the per-symbol bar window."""
    auth = _check_account_token(request, account_id=account_id)
    if isinstance(auth, Response):
        return auth
    _token, token_hash = auth

    if not instrument.strip():
        return canonical_error_response(
            "webapp:missing_query_param:instrument",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not start.strip():
        return canonical_error_response(
            "webapp:missing_query_param:start",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not end.strip():
        return canonical_error_response(
            "webapp:missing_query_param:end",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return canonical_error_response(
            f"webapp:bad_iso_datetime:{start}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return canonical_error_response(
            f"webapp:bad_iso_datetime:{end}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    reader = _bar_reader(request)
    if reader is None:
        return canonical_error_response(
            "webapp:instrument_bar_repository_missing",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    read_result = reader.bars_for(
        account_id=account_id,
        instrument_id=InstrumentId(instrument),
        start=start_dt,
        end=end_dt,
    )
    if isinstance(read_result, Err):
        return canonical_error_response(
            read_result.error,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    bars = read_result.value

    # Best-effort audit — the read endpoint isn't a write, but the
    # token_hash ties operator reads to the CR-024 audit train.
    structured_log(
        _AUDIT_LOGGER,
        logging.INFO,
        "security",
        "bars:read",
        event="bars_read",
        account_id=str(account_id),
        outcome="ok",
        token_hash=token_hash,
        instrument=instrument,
    )

    body = {
        "bars": [
            {
                "at": b.at.isoformat(),
                "open": str(b.open),
                "high": str(b.high),
                "low": str(b.low),
                "close": str(b.close),
                "volume": str(b.volume),
            }
            for b in bars
        ]
    }
    return canonical_json_response(body)
