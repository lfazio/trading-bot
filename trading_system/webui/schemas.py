"""Canonical response schemas + ``JsonResponse`` envelope.

REQ_NF_WEB_002 — read endpoints SHALL be deterministic byte-for-byte
on identical ``(account_id, as_of)`` tuples. We achieve that by:

1. Modelling response payloads as frozen+slotted dataclasses.
2. Routing serialisation through
   ``trading_system.notifications.canonical.canonical_json_line``
   (sorted keys; Decimal-as-TEXT; ISO-8601 datetimes; StrEnum value
   form). Two calls with equal inputs produce byte-identical strings.

The HTTP envelope wraps the canonical body in
``{"status_code": int, "body": <canonical-json string>,
"content_type": "application/json"}`` so the route layer can return a
single shape regardless of payload kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.phase import Phase
from trading_system.models.safety import KillSwitchState
from trading_system.notifications.canonical import canonical_json_line


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JsonResponse:
    """HTTP-layer envelope — the route returns this; the server
    translates it into HTTP bytes."""

    status_code: int
    body: str  # already canonical-JSON
    content_type: str = "application/json"

    def __post_init__(self) -> None:
        if not (100 <= self.status_code < 600):
            raise ValueError(
                f"JsonResponse.status_code must lie in [100, 600), "
                f"got {self.status_code}"
            )

    @classmethod
    def from_canonical(cls, payload: object, *, status_code: int = 200) -> JsonResponse:
        """Wrap ``payload`` in a canonical body. ``payload`` may be a
        dataclass, dict, or any type ``canonical_json_line`` knows
        how to coerce."""
        return cls(status_code=status_code, body=canonical_json_line(payload))

    @classmethod
    def error(cls, status_code: int, reason: str) -> JsonResponse:
        """Wrap a categorised Err into the canonical
        ``{"error": <reason>}`` shape."""
        return cls(
            status_code=status_code,
            body=canonical_json_line({"error": reason}),
        )


def canonical_response(payload: object) -> JsonResponse:
    """Top-level helper for routes that return a 200 OK body."""
    return JsonResponse.from_canonical(payload)


# ---------------------------------------------------------------------------
# Response dataclasses — REQ_F_WEB_002 read endpoints
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecisionLine:
    """One row of the ``LiveStateResponse.recent_decisions`` tuple."""

    at: datetime
    instrument: str
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class LiveStateResponse:
    """REQ_F_WEB_002 / REQ_SDS_WEB_001 — current-state read endpoint."""

    account_id: AccountId
    as_of: datetime
    ks_state: KillSwitchState
    phase: Phase
    open_positions_count: int
    equity_after_tax: Decimal
    recent_decisions: tuple[DecisionLine, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.account_id).strip():
            raise ValueError("LiveStateResponse.account_id must be non-empty")
        if self.open_positions_count < 0:
            raise ValueError(
                f"LiveStateResponse.open_positions_count must be >= 0, "
                f"got {self.open_positions_count}"
            )


@dataclass(frozen=True, slots=True)
class PromoteResponse:
    """REQ_F_WEB_004 — registry promotion mutation response."""

    promoted: bool
    strategy_id: StrategyId
    account_id: AccountId

    def __post_init__(self) -> None:
        if not str(self.strategy_id).strip():
            raise ValueError("PromoteResponse.strategy_id must be non-empty")
        if not str(self.account_id).strip():
            raise ValueError("PromoteResponse.account_id must be non-empty")


# Phase-B follow-ups will add SummaryResponse, BacktestArchiveResponse,
# ImprovementReportHistoryResponse, JobStatusResponse. Each follows
# the same shape: frozen dataclass + canonical_response wrapper.


# Avoid an unused-import warning for ``Any`` — the type is kept
# accessible for callers that wrap pre-built dicts.
_ = Any  # noqa: F841
