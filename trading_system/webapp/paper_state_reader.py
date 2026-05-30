"""Paper-trading state reader — Protocol + RuntimeRegistry-backed concrete.

REQ refs:
- REQ_F_WEB2_003 — paper-trading dashboard panel reads a state
  snapshot per registered paper session.
- REQ_NF_WEB2_001 — read-side determinism: equal inputs ⇒
  byte-identical canonical JSON. The reader is a pure function
  of its inputs at any given moment in time.
- REQ_SDD_FAS_001 — closed import graph. The reader uses a
  Protocol-shaped slot (``PaperRuntimeView``) so the webapp does
  not import the concrete ``PaperTradingRuntime`` at this layer.

Pattern mirrors ``state_readers.py``: the webapp's lifespan
attaches a ``RuntimeRegistry`` to ``app.state``; the SSE handler
asks the reader for an async stream of snapshots, and the
request-response handler asks it for a single snapshot.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId
from trading_system.result import Some
from trading_system.webapp.runtimes.quant_indicators import compute_indicators
from trading_system.webui.schemas import (
    InstrumentRow,
    OpenPositionView,
    PaperStateResponse,
    RecentTradeView,
)


@runtime_checkable
class PaperRuntimeView(Protocol):
    """Read-only surface a paper-trading runtime SHALL expose for
    the dashboard panel. The concrete ``PaperTradingRuntime`` from
    ``trading_system.webapp.runtimes.paper_trading`` satisfies
    this Protocol structurally; tests inject hand-rolled stubs."""

    def is_alive(self) -> bool: ...
    def is_degraded(self) -> bool: ...
    def degraded_since(self) -> datetime | None: ...
    def last_tick_at(self) -> datetime | None: ...
    def equity_history(self) -> tuple: ...  # tuple[EquityPoint, ...]


@runtime_checkable
class PaperRegistryView(Protocol):
    """Read-only surface ``RuntimeRegistry`` exposes — just the
    one lookup the reader needs. Lets tests inject a fake
    registry without dragging in the live-ticking surface."""

    def status(self, account_id: AccountId): ...  # returns Option[runtime]


@dataclass(frozen=True, slots=True)
class RuntimePaperStateReader:
    """Concrete ``PaperStateReader`` over a ``RuntimeRegistry``.

    Construct via the webapp's ``default_app()``; tests construct
    directly with a fake registry. The ``tick_seconds`` parameter
    sets the SSE push cadence (default 2s — the paper panel needs
    to feel live; the existing 5s live-state cadence is for the
    aggregate dashboard).
    """

    registry: PaperRegistryView
    tick_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.tick_seconds <= 0:
            raise ValueError(
                "RuntimePaperStateReader.tick_seconds must be > 0, "
                f"got {self.tick_seconds}"
            )

    def paper_state(
        self,
        *,
        account_id: AccountId,
        as_of: datetime,
        pinned_symbol: str | None = None,
    ) -> PaperStateResponse:
        """Snapshot for one paper-trading session.

        Returns the documented "session_not_found" sentinel (an
        all-zeroed payload with ``is_alive=False``) when the
        registry has no live entry for the requested account_id
        — keeps the SSE stream contract single-shape so HTMX
        doesn't need a separate error path.
        """
        runtime_opt = self.registry.status(account_id)
        if not isinstance(runtime_opt, Some):
            return PaperStateResponse(
                account_id=account_id,
                as_of=as_of,
                is_alive=False,
                is_degraded=False,
                degraded_since=None,
                last_tick_at=None,
                equity_points_count=0,
                latest_equity_after_tax=None,
            )
        runtime = runtime_opt.value
        history = runtime.equity_history()
        if history:
            latest_amount: Decimal | None = history[-1].equity_after_tax.amount
        else:
            latest_amount = None

        # Load the bar-history window ONCE — both the positions
        # view (for per-position sparklines) and the quant-
        # indicators view (for SMA / vol / drawdown) consume it.
        bar_closes, bar_timestamps = _bar_history_for_runtime(runtime)

        # Reference-index window — REQ_F_WEB2_010. Reuses the
        # runtime's market-data provider (which already wraps
        # the yfinance disk cache) to fetch ^FCHI / ^STOXX50E /
        # etc. bars over the same lookback. Empty when the
        # universe declares no index OR the provider can't reach
        # the upstream.
        index_symbol, index_closes, index_timestamps = (
            _index_bars_for_runtime(runtime)
        )
        # Session metadata + live price — best-effort. The Protocol
        # surface (PaperRuntimeView) doesn't pin these so tests with
        # minimal stubs still work; we duck-type via getattr.
        session = getattr(runtime, "session", None)
        universe = getattr(session, "universe", "") if session else ""
        strategy_id = (
            str(getattr(session, "strategy_id", "")) if session else ""
        )
        starting_capital_money = (
            getattr(session, "starting_capital", None) if session else None
        )
        starting_capital_amount: Decimal | None = (
            getattr(starting_capital_money, "amount", None)
            if starting_capital_money is not None
            else None
        )
        instrument = getattr(runtime, "instrument", None)
        instrument_symbol = (
            getattr(instrument, "symbol", "") if instrument else ""
        )
        latest_close: Decimal | None = None
        if hasattr(runtime, "latest_close"):
            try:
                latest_close = runtime.latest_close()
            except Exception:  # noqa: BLE001 — defensive
                latest_close = None
        # Trade + open-positions counts — surfaced for the panel
        # so the operator sees the strategy actually trading.
        trades_count = 0
        if hasattr(runtime, "trade_history"):
            try:
                trades_count = len(runtime.trade_history())
            except Exception:  # noqa: BLE001
                trades_count = 0
        open_positions_count = 0
        open_positions_view: tuple[OpenPositionView, ...] = ()
        portfolio = getattr(runtime, "portfolio", None)
        if portfolio is not None and hasattr(portfolio, "positions"):
            try:
                positions = portfolio.positions()
                live_positions = [
                    p for p in positions.values() if getattr(p, "quantity", 0) != 0
                ]
                open_positions_count = len(live_positions)
                # Per-position sparkline window — cap at 30 bars
                # so the SSE payload stays tight even with many
                # open positions. v1 runtime trades a single
                # instrument per session so all rows share the
                # same series; a future multi-instrument runtime
                # would key the window by instrument id.
                position_window = 30
                shared_series = (
                    tuple(bar_closes[-position_window:]) if bar_closes else ()
                )
                latest_in_series = (
                    bar_closes[-1] if bar_closes else None
                )
                rows: list[OpenPositionView] = []
                for p in live_positions:
                    avg = getattr(p, "avg_price", None)
                    pnl_pct: Decimal | None = None
                    if (
                        latest_in_series is not None
                        and avg is not None
                        and avg > 0
                    ):
                        pnl_pct = (
                            (latest_in_series - avg) / avg * Decimal("100")
                        ).quantize(Decimal("0.01"))
                    rows.append(
                        OpenPositionView(
                            instrument_symbol=getattr(
                                getattr(p, "instrument", None), "symbol", ""
                            ),
                            quantity=p.quantity,
                            avg_price=p.avg_price,
                            recent_close_series=shared_series,
                            latest_close=latest_in_series,
                            unrealized_pnl_pct=pnl_pct,
                        )
                    )
                open_positions_view = tuple(rows)
            except Exception:  # noqa: BLE001
                open_positions_count = 0
                open_positions_view = ()

        # Build the recent-trades view — last 10 trades.
        recent_view: tuple[RecentTradeView, ...] = ()
        if hasattr(runtime, "trade_history") and hasattr(runtime, "order_for_trade"):
            try:
                trades = list(runtime.trade_history())
                tail = trades[-10:]
                items: list[RecentTradeView] = []
                for t in tail:
                    order = runtime.order_for_trade(str(t.id))
                    side = (
                        getattr(getattr(order, "side", None), "value", "")
                        .upper() if order is not None else ""
                    )
                    if side not in ("BUY", "SELL"):
                        continue  # skip malformed
                    items.append(
                        RecentTradeView(
                            trade_id=str(t.id),
                            executed_at=t.executed_at,
                            side=side,
                            instrument_symbol=getattr(
                                getattr(order, "instrument", None),
                                "symbol",
                                "",
                            ),
                            quantity=t.quantity_filled,
                            price=t.price,
                            fees=t.fees.amount,
                        )
                    )
                recent_view = tuple(items)
            except Exception:  # noqa: BLE001
                recent_view = ()
        # CR-026 — per-instrument grid rows. The runtime's `universe`
        # attribute (lex-sorted by symbol per REQ_SDD_PAP_006) is the
        # canonical source. Each row pulls best-effort live close +
        # day-change from the wrapped MarketDataProvider; the
        # `has_open_position` flag joins against the live positions.
        per_instrument_rows = _per_instrument_rows(
            runtime, live_positions_by_symbol(portfolio)
        )
        # Pin resolution (REQ_F_PAP_018 / REQ_SDD_PAP_010):
        #   - operator-supplied ``pinned_symbol`` wins iff it's in
        #     the universe;
        #   - otherwise default to the first symbol in lex order.
        symbols_in_universe = {r.symbol for r in per_instrument_rows}
        if pinned_symbol and pinned_symbol in symbols_in_universe:
            resolved_pin = pinned_symbol
        elif per_instrument_rows:
            resolved_pin = per_instrument_rows[0].symbol
        else:
            resolved_pin = ""

        return PaperStateResponse(
            account_id=account_id,
            as_of=as_of,
            is_alive=runtime.is_alive(),
            is_degraded=runtime.is_degraded(),
            degraded_since=runtime.degraded_since(),
            last_tick_at=runtime.last_tick_at(),
            equity_points_count=len(history),
            latest_equity_after_tax=latest_amount,
            universe=universe,
            strategy_id=strategy_id,
            starting_capital=starting_capital_amount,
            instrument_symbol=instrument_symbol,
            latest_close=latest_close,
            trades_count=trades_count,
            open_positions_count=open_positions_count,
            recent_trades=recent_view,
            open_positions=open_positions_view,
            index_symbol=index_symbol,
            index_close_series=tuple(index_closes[-60:]) if index_closes else (),
            index_close_timestamps=(
                tuple(index_timestamps[-60:]) if index_timestamps else ()
            ),
            per_instrument=per_instrument_rows,
            pinned_symbol=resolved_pin,
            **_indicator_kwargs(runtime, history, bar_closes, bar_timestamps),
        )

    async def subscribe(
        self,
        *,
        account_id: AccountId,
        pinned_symbol: str | None = None,
    ) -> AsyncIterator[PaperStateResponse]:
        """Yield one snapshot every ``tick_seconds``.

        The handler exits the loop when the request disconnects
        (the SSE router checks ``request.is_disconnected()`` and
        breaks out of ``async for`` on the first ``True``).

        ``pinned_symbol`` is threaded through to ``paper_state``
        on every tick so the SSE stream's ``pinned_symbol`` field
        reflects the operator's pin (REQ_SDD_PAP_010).
        """
        while True:
            yield self.paper_state(
                account_id=account_id,
                as_of=datetime.now(tz=UTC),
                pinned_symbol=pinned_symbol,
            )
            await asyncio.sleep(self.tick_seconds)


# ---------------------------------------------------------------------------
# CR-026 — per-instrument grid rows
# ---------------------------------------------------------------------------


def live_positions_by_symbol(portfolio) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """Return ``{symbol: Position}`` for every non-zero position
    in the portfolio. Empty dict when ``portfolio`` is None or has
    no `.positions()` accessor (test stubs)."""
    if portfolio is None or not hasattr(portfolio, "positions"):
        return {}
    try:
        positions = portfolio.positions()
    except Exception:  # noqa: BLE001 — defensive
        return {}
    out: dict[str, object] = {}
    for p in positions.values():
        if getattr(p, "quantity", 0) == 0:
            continue
        symbol = getattr(getattr(p, "instrument", None), "symbol", "")
        if symbol:
            out[symbol] = p
    return out


def _per_instrument_rows(  # type: ignore[no-untyped-def]
    runtime, positions_by_symbol: dict[str, object]
) -> tuple[InstrumentRow, ...]:
    """REQ_F_PAP_017 / REQ_SDD_PAP_009 — build the per-instrument
    grid rows.

    Reads the runtime's ``universe`` attribute (lex-sorted per
    REQ_SDD_PAP_006). For each stock, queries the wrapped
    MarketDataProvider for the latest bar (best-effort; missing
    data ⇒ ``None`` fields). Joins against the portfolio's open
    positions for the ``has_open_position`` flag.

    Empty tuple when the runtime carries no universe (test stubs
    without the field) — the dashboard panel falls back to the
    legacy ``instrument_symbol`` surface in that case.
    """
    universe = getattr(runtime, "universe", ())
    if not universe:
        return ()
    provider = getattr(runtime, "market_data_provider", None)
    rows: list[InstrumentRow] = []
    for stock in universe:
        symbol = getattr(stock, "symbol", "")
        if not symbol:
            continue
        last_close: Decimal | None = None
        day_change_pct: Decimal | None = None
        sparkline: tuple[Decimal, ...] = ()
        if provider is not None and hasattr(provider, "latest"):
            try:
                result = provider.latest(stock)
                # Result-shaped: Ok(bar) carries .value with .close/.open;
                # Err carries .error — duck-type so test stubs work.
                if hasattr(result, "value") and not hasattr(result, "error"):
                    bar = result.value
                    last_close = getattr(bar, "close", None)
                    bar_open = getattr(bar, "open", None)
                    if (
                        last_close is not None
                        and bar_open is not None
                        and bar_open > 0
                    ):
                        day_change_pct = (
                            (last_close - bar_open) / bar_open * Decimal("100")
                        ).quantize(Decimal("0.01"))
            except Exception:  # noqa: BLE001 — defensive
                last_close = None
                day_change_pct = None
        rows.append(
            InstrumentRow(
                symbol=symbol,
                last_close=last_close,
                day_change_pct=day_change_pct,
                has_open_position=symbol in positions_by_symbol,
                sparkline=sparkline,
            )
        )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Bar-history loader — shared between positions + indicators
# ---------------------------------------------------------------------------


def _index_bars_for_runtime(  # type: ignore[no-untyped-def]
    runtime,
) -> tuple[str, list, list]:
    """Fetch the runtime's reference-index bar window.

    Returns ``(symbol, closes, timestamps)``. Empty when the
    runtime carries no ``reference_index`` OR the provider can't
    reach the upstream. The window matches the primary
    instrument's lookback so both charts share the same axis.
    """
    index = getattr(runtime, "reference_index", None)
    if index is None:
        return "", [], []
    symbol = getattr(index, "symbol", "") or getattr(index, "id", "")
    provider = getattr(runtime, "market_data_provider", None)
    if provider is None:
        return str(symbol), [], []
    try:
        from trading_system.webapp.runtimes.provider_bar_window import (
            fetch_recent_close_window,
        )

        closes, timestamps = fetch_recent_close_window(
            provider, index, days=120
        )
    except Exception:  # noqa: BLE001 — defensive
        return str(symbol), [], []
    return str(symbol), closes, timestamps


def _rolling_sma(values: list, window: int) -> list:
    """Return a parallel list with the rolling SMA at every index;
    indices that don't have enough history hold ``None``."""
    out: list = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        head = values[i + 1 - window:i + 1]
        out.append(sum(head, start=Decimal("0")) / Decimal(window))
    return out


def _volume_history_for_runtime(runtime) -> list:  # type: ignore[no-untyped-def]
    """Pull the volume series parallel to the close series.

    Returns ``[]`` when the bar source doesn't keep volume + the
    provider can't be reached. Splices into the dashboard so the
    chart's volume row stays aligned with the price line.
    """
    bar_source = getattr(runtime, "bar_source", None)
    if bar_source is None:
        return []
    if hasattr(bar_source, "history"):
        try:
            return [b.volume for b in bar_source.history()]
        except Exception:  # noqa: BLE001
            return []
    return []  # yfinance source: volume not surfaced through the bar-window helper yet


def _bar_history_for_runtime(runtime) -> tuple[list, list]:  # type: ignore[no-untyped-def]
    """Load the bar-close window the runtime can expose. Two paths:

    1. ``bar_source.history()`` — the simulated bar source keeps a
       full history of every emitted bar.
    2. ``bar_source._provider`` — the yfinance source only keeps
       the most recent bar; this fetches a 120-day window through
       the wrapped MarketDataProvider.

    Returns ``(closes, timestamps)`` lists. Empty on any failure
    so callers can render the empty-state placeholder.
    """
    bar_source = getattr(runtime, "bar_source", None)
    if bar_source is None:
        return [], []
    if hasattr(bar_source, "history"):
        try:
            bar_history = bar_source.history()
            return [b.close for b in bar_history], [b.at for b in bar_history]
        except Exception:  # noqa: BLE001
            return [], []
    if hasattr(bar_source, "_provider"):
        from trading_system.webapp.runtimes.provider_bar_window import (
            fetch_recent_close_window,
        )

        instrument = getattr(runtime, "instrument", None)
        if instrument is not None:
            return fetch_recent_close_window(
                bar_source._provider,  # noqa: SLF001
                instrument,
                days=120,
            )
    return [], []


# ---------------------------------------------------------------------------
# Quant-indicator extraction helper
# ---------------------------------------------------------------------------


def _indicator_kwargs(runtime, history, closes, bar_timestamps) -> dict:  # type: ignore[no-untyped-def]
    """Compute the quant indicators using the pre-loaded
    ``(closes, timestamps)`` window. ``history`` is the equity-
    point list; ``closes`` is the bar-close series the caller
    already loaded via ``_bar_history_for_runtime``.

    On any failure (no equity), every field falls back to its
    documented ``None`` / ``"n/a"`` sentinel.
    """
    equity_amounts = [p.equity_after_tax.amount for p in history] if history else []
    regime = "n/a"
    rgm = getattr(runtime, "regime", None)
    if rgm is not None:
        # MarketRegime is a StrEnum with lowercase values; upper-case
        # at the boundary so the dashboard renders BULL / BEAR /
        # HIGH_VOL / SIDEWAYS as documented.
        raw = getattr(rgm, "value", str(rgm))
        regime = raw.upper() if isinstance(raw, str) else str(raw)
    snap = compute_indicators(closes, equity_amounts, regime=str(regime))
    # Cap the surfaced series at the last 60 bars so the SSE
    # payload stays bounded. Compute the SMA(20/50) overlays
    # on the FULL close series first so the rolling means see
    # enough history, then trim to the same 60-bar window.
    series_window = 60
    if closes:
        sma20_full = _rolling_sma(closes, 20)
        sma50_full = _rolling_sma(closes, 50)
    else:
        sma20_full = []
        sma50_full = []
    volumes = _volume_history_for_runtime(runtime) if closes else []
    recent_closes = tuple(closes[-series_window:]) if closes else ()
    recent_ts = (
        tuple(bar_timestamps[-series_window:]) if bar_timestamps else ()
    )
    recent_volumes = tuple(volumes[-series_window:]) if volumes else ()
    recent_sma20 = tuple(sma20_full[-series_window:]) if sma20_full else ()
    recent_sma50 = tuple(sma50_full[-series_window:]) if sma50_full else ()
    return {
        "sma_20": snap.sma_20,
        "sma_50": snap.sma_50,
        "realized_vol_pct": snap.realized_vol_pct,
        "total_return_pct": snap.total_return_pct,
        "drawdown_pct": snap.drawdown_pct,
        "sharpe_ratio": snap.sharpe_ratio,
        "trend_signal": snap.trend_signal,
        "regime": snap.regime,
        "recent_close_series": recent_closes,
        "recent_close_timestamps": recent_ts,
        "recent_volume_series": recent_volumes,
        "recent_sma20_series": recent_sma20,
        "recent_sma50_series": recent_sma50,
    }
