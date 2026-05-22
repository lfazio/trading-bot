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

    def render_canonical(self) -> str:
        """REQ_SDD_WEB_007 — canonical-JSON body for this row.

        Sorted keys + ``Decimal`` as string + ISO-8601 datetimes
        with timezone via the project-wide canonical serialiser.
        Two calls with identical inputs produce byte-identical
        strings (REQ_NF_WEB_002 family)."""
        return canonical_json_line(self)


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

    def render_canonical(self) -> str:
        """REQ_SDD_WEB_007 — canonical-JSON body. Sorted keys +
        Decimal as string + ISO-8601 datetimes with UTC offset.
        The HTTP envelope's request_id / server_timestamp are
        appended OUTSIDE this body so they don't break the
        REQ_NF_WEB_002 byte-identical-replay contract."""
        return canonical_json_line(self)


@dataclass(frozen=True, slots=True)
class PaperStateResponse:
    """REQ_F_WEB2_003 — current-state read shape for a paper-trading
    session.

    Surfaces just what the dashboard panel renders:
    - ``account_id`` / ``as_of`` for the SSE envelope (`as_of`
      doubles as the SSE event id so `hx-sse` resumes after
      disconnect).
    - ``is_alive`` — false ⇒ the session has stopped; the panel
      shows a "session ended" badge.
    - ``is_degraded`` + ``degraded_since`` — REQ_F_PAP_002
      cached-only banner.
    - ``last_tick_at`` — operator-visible "freshness" indicator.
    - ``equity_points_count`` — cardinality of the in-memory
      equity series (matches `len(runtime.equity_history())`).
    - ``latest_equity_after_tax`` — the freshest after-tax
      equity reading; ``None`` until the first tick records a
      point (the panel shows a placeholder).

    Field order is deliberately stable (alphabetical via canonical
    JSON) so REQ_NF_WEB2_001 byte-identical-replay holds.
    """

    account_id: AccountId
    as_of: datetime
    is_alive: bool
    is_degraded: bool
    degraded_since: datetime | None
    last_tick_at: datetime | None
    equity_points_count: int
    latest_equity_after_tax: Decimal | None
    # Session config — surfaced so the dashboard panel renders the
    # operator's choices alongside the live numbers (empty strings
    # when no session is registered for this account_id).
    universe: str = ""
    strategy_id: str = ""
    starting_capital: Decimal | None = None
    instrument_symbol: str = ""
    latest_close: Decimal | None = None

    def __post_init__(self) -> None:
        if not str(self.account_id).strip():
            raise ValueError("PaperStateResponse.account_id must be non-empty")
        if self.equity_points_count < 0:
            raise ValueError(
                "PaperStateResponse.equity_points_count must be >= 0, "
                f"got {self.equity_points_count}"
            )

    def render_canonical(self) -> str:
        return canonical_json_line(self)


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

    def render_canonical(self) -> str:
        """REQ_SDD_WEB_007 — canonical-JSON body for the promotion
        response. Same sorted-key + Decimal-as-string contract as
        the read responses; envelope fields go outside."""
        return canonical_json_line(self)


# Phase-B follow-ups will add SummaryResponse, BacktestArchiveResponse,
# ImprovementReportHistoryResponse, JobStatusResponse. Each follows
# the same shape: frozen dataclass + canonical_response wrapper.


# Avoid an unused-import warning for ``Any`` — the type is kept
# accessible for callers that wrap pre-built dicts.
_ = Any  # noqa: F841
