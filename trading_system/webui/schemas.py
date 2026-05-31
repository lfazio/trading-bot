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
class RecentTradeView:
    """Compact trade summary for the paper-trading panel
    (REQ_F_WEB2_003 — "recent decisions" surface)."""

    trade_id: str
    executed_at: datetime
    side: str  # "BUY" / "SELL"
    instrument_symbol: str
    quantity: Decimal
    price: Decimal
    fees: Decimal

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise ValueError(
                f"RecentTradeView.side must be BUY or SELL, got {self.side!r}"
            )
        if self.quantity <= 0:
            raise ValueError(
                f"RecentTradeView.quantity must be > 0, got {self.quantity}"
            )


@dataclass(frozen=True, slots=True)
class OpenPositionView:
    """Compact open-position summary for the paper-trading panel.

    ``recent_close_series`` carries the last ~30 close prices for
    the position's instrument so the dashboard can render an
    inline SVG sparkline next to the position row. Empty when no
    bar history is available for the instrument yet.
    """

    instrument_symbol: str
    quantity: Decimal  # signed
    avg_price: Decimal
    recent_close_series: tuple[Decimal, ...] = ()
    latest_close: Decimal | None = None
    unrealized_pnl_pct: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SRDPositionRow:
    """CR-030 — open SRD position row for the dashboard's
    deferred-settlement panel.

    Fields:
    - ``instrument_symbol`` — display symbol (e.g., ``AC``).
    - ``direction`` — ``"LONG"`` or ``"SHORT"``.
    - ``quantity`` — always positive (direction encodes sign).
    - ``entry_price`` — settled at open.
    - ``latest_close`` — most-recent mark; ``None`` until the
      next tick records one.
    - ``unrealized_pnl_pct`` — (latest − entry)/entry × 100,
      negated for SHORT; ``None`` until ``latest_close`` is set.
    - ``settlement_at`` — last business day of the entry month
      (ISO-8601 UTC); the dashboard renders this as the
      "next settlement" countdown.
    - ``estimated_crd_fee`` — ``quantity × entry_price ×
      carry_fee_rate_monthly``. Operator-visible total the CRD
      will deduct on settlement day.
    - ``auto_rollover`` — true when the position is set to roll
      to next month at settlement.
    """

    instrument_symbol: str
    direction: str
    quantity: Decimal
    entry_price: Decimal
    settlement_at: datetime
    estimated_crd_fee: Decimal
    auto_rollover: bool = False
    latest_close: Decimal | None = None
    unrealized_pnl_pct: Decimal | None = None


@dataclass(frozen=True, slots=True)
class InstrumentRow:
    """CR-026 (REQ_F_PAP_017 / REQ_SDD_PAP_009) — per-instrument
    state for the dashboard grid.

    One row per stock in the runtime's configured universe. The
    operator sees every symbol's state at a glance instead of just
    the runtime's pinned instrument. The dashboard's per-instrument
    grid renders one ``<tr>`` per row + a click handler that pins
    the symbol as the detail chart's data source.

    Fields:
    - ``symbol`` — the stock's display symbol (e.g., "AC", "AIR").
    - ``last_close`` — most recent close from the bar source;
      ``None`` until a poll succeeds.
    - ``day_change_pct`` — percentage change from the day's open;
      ``None`` until the day's first close is available.
    - ``has_open_position`` — true iff the portfolio carries a
      non-zero position in this instrument.
    - ``sparkline`` — last ≤16 closes, oldest-first; empty when no
      history is available yet.
    """

    symbol: str
    last_close: Decimal | None
    day_change_pct: Decimal | None
    has_open_position: bool
    sparkline: tuple[Decimal, ...] = ()


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
    trades_count: int = 0
    open_positions_count: int = 0
    recent_trades: tuple[RecentTradeView, ...] = ()
    open_positions: tuple[OpenPositionView, ...] = ()
    # Quant indicators panel — see
    # ``trading_system.webapp.runtimes.quant_indicators.QuantIndicators``
    # for the computation. All Decimal fields are nullable until
    # the simulator has enough bars / equity samples.
    sma_20: Decimal | None = None
    sma_50: Decimal | None = None
    realized_vol_pct: Decimal | None = None
    total_return_pct: Decimal | None = None
    drawdown_pct: Decimal | None = None
    sharpe_ratio: Decimal | None = None
    trend_signal: str = "n/a"
    regime: str = "n/a"
    # REQ_F_WEB2_010 follow-up — recent close prices (last ~60
    # bars) for the runtime's primary instrument. Surfaced as
    # parallel arrays so the dashboard can render an inline
    # sparkline without re-fetching. Empty when no bars are
    # available yet.
    recent_close_series: tuple[Decimal, ...] = ()
    recent_close_timestamps: tuple[datetime, ...] = ()
    # Companion series — same length as recent_close_series so
    # the dashboard can overlay volume bars + SMA(20/50) lines on
    # the main price chart.
    recent_volume_series: tuple[Decimal, ...] = ()
    # SMA windows that don't have enough history yet emit ``None``
    # at that position so the chart can render the SMA line only
    # where it's defined.
    recent_sma20_series: tuple[Decimal | None, ...] = ()
    recent_sma50_series: tuple[Decimal | None, ...] = ()
    # REQ_F_WEB2_010 follow-up — reference-index series so the
    # dashboard's main chart shows the broader market context
    # (e.g. ^FCHI for cac40, ^STOXX50E for eu-dividend-starter)
    # instead of the first stock the runtime happens to be
    # trading. Empty when the universe declares no ``indices:``.
    index_symbol: str = ""
    index_close_series: tuple[Decimal, ...] = ()
    index_close_timestamps: tuple[datetime, ...] = ()
    # CR-026 follow-up — reference-index volume strip + VIX overlay.
    # The dashboard renders the volume strip below the index price
    # line and overlays the VIX series so the operator sees the
    # vol-regime context alongside the broader market.
    index_volume_series: tuple[Decimal, ...] = ()
    vix_symbol: str = ""
    vix_close_series: tuple[Decimal, ...] = ()
    vix_close_timestamps: tuple[datetime, ...] = ()
    # CR-026 (REQ_F_PAP_017 / REQ_SDD_PAP_009) — per-instrument
    # grid. One ``InstrumentRow`` per stock in the runtime's
    # configured universe, sorted by ``symbol`` lex-order so the
    # dashboard render is order-stable across SSE pushes. Empty
    # tuple ⇒ legacy single-instrument session (the existing
    # ``instrument_symbol`` field remains the source of truth in
    # that case).
    per_instrument: tuple[InstrumentRow, ...] = ()
    # CR-026 (REQ_F_PAP_018 / REQ_SDD_PAP_010) — which symbol the
    # dashboard's detail chart is currently pinned to. The default
    # is the first symbol in ``per_instrument`` (lex order); the
    # ``?pin=<symbol>`` query parameter overrides on click.
    pinned_symbol: str = ""
    # CR-030 — SRD positions surfaced separately from the cash
    # positions table so the operator sees the open SRD exposure
    # + next-settlement-date + estimated CRD-fee summary. Empty
    # tuple when no SRD positions are open.
    srd_positions: tuple["SRDPositionRow", ...] = ()
    srd_settlements_count: int = 0

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


# ---------------------------------------------------------------------------
# Phase-B read response schemas — REQ_F_WEB_002 (b/c/d/e)
# ---------------------------------------------------------------------------
#
# Four response shapes mirroring the FastAPI surface's coverage of
# REQ_F_WEB_002 (b) financial summary, (c) strategy registry,
# (d) backtest archive, (e) ImprovementReport history. Each frozen
# dataclass routes through ``canonical_json_line`` so the read
# endpoints satisfy REQ_NF_WEB_002 byte-determinism.


@dataclass(frozen=True, slots=True)
class SummaryResponse:
    """REQ_F_WEB_002 (b) — financial summary read endpoint.

    Captures the operator-visible after-tax equity snapshot plus
    the realised / unrealised PnL split + max drawdown observed
    so far. The full equity curve lives behind a separate
    paginated endpoint when the dataset grows; v1 ships the
    summary point + ``as_of`` so the operator can chart deltas
    between polls.
    """

    account_id: AccountId
    as_of: datetime
    equity_after_tax: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    dividend_income_ytd: Decimal
    max_drawdown_pct: Decimal


@dataclass(frozen=True, slots=True)
class RegistryEntryLine:
    """One row of ``RegistryListResponse.entries``.

    Mirrors the persistence-layer ``RegistryEntry`` shape without
    the heavy metric vectors — the dashboard fetches per-entry
    detail through a separate endpoint when the operator drills
    in.
    """

    strategy_id: StrategyId
    git_sha: str
    config_hash: str
    validated: bool
    promoted_at: datetime


@dataclass(frozen=True, slots=True)
class RegistryListResponse:
    """REQ_F_WEB_002 (c) — strategy registry read endpoint.

    Lists every registry entry visible to the operator. Sorted
    by ``(promoted_at desc, strategy_id asc)`` for stable
    pagination; the ``validated`` field surfaces the
    operator-promotion gate (REQ_F_PER_006).
    """

    account_id: AccountId
    as_of: datetime
    entries: tuple[RegistryEntryLine, ...]


@dataclass(frozen=True, slots=True)
class BacktestArchiveLine:
    """One row of ``BacktestsArchiveResponse.entries``."""

    strategy_id: StrategyId
    git_sha: str
    config_hash: str
    seed: int
    final_equity_after_tax: Decimal
    max_drawdown_pct: Decimal
    sharpe: Decimal
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestsArchiveResponse:
    """REQ_F_WEB_002 (d) — backtest archive read endpoint.

    Every ``BacktestResult`` archived through the CR-008
    ``BacktestResultRepository`` keyed on
    ``(strategy_id, git_sha, config_hash, seed)``. The
    paginated v1 returns the most-recent ``per_page`` rows;
    the operator drills into a single row through a separate
    endpoint when needed.
    """

    account_id: AccountId
    as_of: datetime
    entries: tuple[BacktestArchiveLine, ...]
    per_page: int
    page: int


@dataclass(frozen=True, slots=True)
class ImprovementReportLine:
    """One row of ``ImprovementReportsHistoryResponse.reports``."""

    cycle_id: str
    created_at: datetime
    git_sha: str
    accepted_count: int
    rejected_count: int


@dataclass(frozen=True, slots=True)
class ImprovementReportsHistoryResponse:
    """REQ_F_WEB_002 (e) — meta-loop ImprovementReport history.

    Lists every report emitted by ``LoopController.cycle`` ordered
    by ``created_at`` descending. v1 returns the summary line
    (counts + cycle id); the operator drills in for the full
    accepted / rejected detail through a separate endpoint.
    """

    account_id: AccountId
    as_of: datetime
    reports: tuple[ImprovementReportLine, ...]


# Avoid an unused-import warning for ``Any`` — the type is kept
# accessible for callers that wrap pre-built dicts.
_ = Any  # noqa: F841
