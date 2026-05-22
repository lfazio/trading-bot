"""``PaperTradingSession`` + ``PaperTradingRuntime`` + ``RuntimeRegistry``.

CR-019 step 1 (a) — paper-trading runtime mode.

This module composes the existing simulation surface
(``LocalBrokerAdapter`` + ``Portfolio``) with a live ``BarSource``
so the operator runs a session against real market data without
putting money at risk. Per REQ_F_PAP_001 / REQ_F_BRK_003 this is
**a runtime wrapper, not a new BrokerAdapter concrete class** —
no live-adapter discipline is broken.

The v1 surface is deliberately small:

- ``PaperTradingSession`` — frozen identity card for a session.
- ``PaperTradingRuntime`` — composes broker + portfolio +
  one strategy + one instrument; ``tick_once`` is the unit of
  work.
- ``RuntimeRegistry`` — keyed on ``account_id``; rejects
  duplicate live ticks per REQ_F_PAP_005.

REQ refs:
- REQ_F_PAP_001 — runtime wrapper, not a new adapter.
- REQ_F_PAP_002 — graceful degradation to cached-only mode
  when the live ``BarSource`` returns an upstream-block Err.
- REQ_F_PAP_003 — sessions persist across webapp restart via
  the CR-008 ``PortfolioRepository`` slot (interface kept
  minimal in v1; the persistence wiring lands in step (b)).
- REQ_F_PAP_004 — ``paper-<utc-iso-timestamp>`` account_id
  namespace partitions paper rows from any future live-session
  rows.
- REQ_F_PAP_005 — one live-ticking session per account_id at a
  time.
- REQ_SDS_WEB2_004 — three top-level classes; ``tick_once`` /
  ``stop`` / ``is_alive`` / ``status`` / ``resume_from_persistence``.
- REQ_SDD_WEB2_003 — class layout.
- REQ_SDD_WEB2_004 — yfinance graceful degradation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from trading_system.backtesting.market_replay import _bar_to_tick
from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.slippage import ZeroSlippageModel
from trading_system.execution.types import Tick
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import AccountId, InstrumentId, StrategyId
from trading_system.models.instrument import Instrument
from trading_system.models.money import Money
from trading_system.models.phase import AllocationBucket, MarketRegime, PhaseConstraints
from trading_system.persistence.repositories.portfolio import PortfolioRepository
from trading_system.portfolio.portfolio import Portfolio
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.strategies.protocol import Strategy
from trading_system.strategies.state import MarketState


PAPER_ACCOUNT_PREFIX = "paper-"


def new_paper_account_id(now: Callable[[], datetime] = lambda: datetime.now(tz=UTC)) -> AccountId:
    """Generate a fresh ``paper-<utc-iso-timestamp>`` account_id
    per REQ_F_PAP_004."""
    return AccountId(f"{PAPER_ACCOUNT_PREFIX}{now().isoformat()}")


# ---------------------------------------------------------------------------
# BarSource Protocol — abstracts the live data feed
# ---------------------------------------------------------------------------


@runtime_checkable
class BarSource(Protocol):
    """Polls the next bar for the configured instrument.

    The concrete implementation is the CR-009 yfinance adapter
    (via a thin wrapper that calls ``provider.bars`` with a
    rolling 1-bar window); tests inject a stub that yields from
    a list.

    ``next_bar`` returns:

    - ``Ok(Some(bar))`` when a new bar is available;
    - ``Ok(Nothing())`` when no new bar has arrived since the
      last call (the runtime sleeps + retries);
    - ``Err("data:upstream_blocked")`` / ``Err("network:timeout")``
      when the upstream fetch fails — the runtime catches these
      and falls back to the cache (REQ_F_PAP_002).
    """

    def next_bar(self) -> Result[Option[Bar], str]: ...
    def latest_cached(self) -> Result[Option[Bar], str]: ...


# ---------------------------------------------------------------------------
# Session identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperTradingSession:
    """Frozen identity card for a paper-trading session
    (REQ_SDS_WEB2_004 / REQ_SDD_WEB2_003).

    Mode is pinned to ``"paper"`` at construction so a single
    field flip can't accidentally re-use a paper session under
    a different mode. Future live-session amendments add a
    sibling ``LiveTradingSession`` rather than mutating this one.
    """

    account_id: AccountId
    universe: str
    strategy_id: StrategyId
    starting_capital: Money
    started_at: datetime
    mode_tag: Literal["paper"] = "paper"

    def __post_init__(self) -> None:
        if not str(self.account_id).startswith(PAPER_ACCOUNT_PREFIX):
            raise ValueError(
                "PaperTradingSession.account_id must start with "
                f"{PAPER_ACCOUNT_PREFIX!r} (REQ_F_PAP_004); got "
                f"{self.account_id!r}"
            )
        if not self.universe.strip():
            raise ValueError("PaperTradingSession.universe must be non-empty")
        if not str(self.strategy_id).strip():
            raise ValueError("PaperTradingSession.strategy_id must be non-empty")
        if self.starting_capital.amount <= 0:
            raise ValueError(
                "PaperTradingSession.starting_capital must be > 0, "
                f"got {self.starting_capital.amount}"
            )


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PaperTradingRuntime:
    """One live-ticking paper-trading runtime.

    Construct via :func:`build_runtime` so the broker + portfolio
    are wired identically across call sites. Tests construct
    directly with stubs.

    The runtime is single-threaded by design — ``tick_once`` is
    the unit of work. The webapp's lifespan task drives the
    runtime via an asyncio sleep loop (see CR-019 SDD §11.20.3);
    persistence + SSE push happen inside ``tick_once`` after the
    engine step.
    """

    session: PaperTradingSession
    instrument: Instrument
    strategy: Strategy
    bar_source: BarSource
    broker: LocalBrokerAdapter
    portfolio: Portfolio
    phase_constraints: PhaseConstraints
    regime: MarketRegime
    # Optional ``MarketDataProvider`` slot fed into ``MarketState.market``
    # so strategies that consult the historical bar window (e.g.,
    # ``TacticalStrategy``) still find their lookback. ``None`` is fine
    # for strategies that only read ``state.portfolio`` (e.g.,
    # ``CoreStrategy``); v1 wiring lands the live yfinance provider here
    # in step (b).
    market_data_provider: MarketDataProvider | None = None
    # CR-019 step 1 (b) — REQ_F_PAP_003: when the operator wires a
    # ``PortfolioRepository``, every recorded equity point lands in
    # SQLite under the session's ``account_id``. ``None`` keeps the
    # runtime in pure in-memory mode (tests, smoke checks, the v1
    # demo path before the webapp owns a connection). Persistence
    # failures DO NOT halt the tick — they surface as the tick's
    # ``Err`` so the dashboard can show "saving disabled" without
    # ripping the live session apart.
    equity_repo: PortfolioRepository | None = None
    spread_pct: Decimal = Decimal("0.001")

    _alive: bool = field(default=True, init=False)
    _degraded_since: datetime | None = field(default=None, init=False)
    _last_tick_at: datetime | None = field(default=None, init=False)
    _equity_points: list[EquityPoint] = field(default_factory=list, init=False)

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        """REQ_SDS_WEB2_004 — registry health-check."""
        return self._alive

    def is_degraded(self) -> bool:
        """REQ_F_PAP_002 — cached-only banner state.

        ``True`` from the moment the live ``BarSource`` returned
        an upstream-block / timeout Err and the runtime fell
        back to cached bars. Reset to ``False`` if a subsequent
        ``next_bar`` succeeds (operator-visible signal: yfinance
        is back)."""
        return self._degraded_since is not None

    def degraded_since(self) -> datetime | None:
        return self._degraded_since

    def last_tick_at(self) -> datetime | None:
        return self._last_tick_at

    def equity_history(self) -> tuple[EquityPoint, ...]:
        """Read-only view of the equity series accumulated so far."""
        return tuple(self._equity_points)

    def latest_close(self) -> Decimal | None:
        """Most recent bar close emitted by the BarSource — surfaced
        to the dashboard panel so the operator sees the live price
        even when no equity point has been recorded yet."""
        try:
            cached = self.bar_source.latest_cached()
        except Exception:  # noqa: BLE001 — defensive
            return None
        match cached:
            case Ok(Some(bar)):
                return bar.close
            case _:
                return None

    def stop(self) -> None:
        """REQ_SDS_WEB2_004 — operator-driven session stop."""
        self._alive = False

    def tick_once(self) -> Result[Option[EquityPoint], str]:
        """Drive one tick.

        Returns:
          - ``Ok(Some(equity_point))`` when a fresh bar drove a
            full tick + the broker + strategy + portfolio + an
            equity point was recorded.
          - ``Ok(Nothing())`` when ``BarSource.next_bar`` reported
            "no new bar yet" — the caller sleeps and retries.
          - ``Err("paper:session_stopped")`` when the operator
            previously called ``stop`` — the loop owner SHALL
            remove the runtime from the registry.
          - ``Err("paper:no_cached_data")`` when the upstream
            fetch failed AND the cache has no bar to fall back
            to (REQ_SDD_WEB2_004).
        """
        if not self._alive:
            return Err("paper:session_stopped")

        bar_result = self.bar_source.next_bar()
        bar = self._resolve_bar(bar_result)
        if isinstance(bar, Err):
            return bar
        if isinstance(bar, Ok) and bar.value is None:
            # No new bar yet; caller retries.
            return Ok(Nothing())

        # Apply the bar.
        live_bar = bar.value if isinstance(bar, Ok) else None
        if live_bar is None:  # safety: should not occur given the branch above
            return Ok(Nothing())
        return self._apply_bar(live_bar)

    # ------------------------------------------------------------------
    # Bar-resolution helpers
    # ------------------------------------------------------------------

    def _resolve_bar(
        self, bar_result: Result[Option[Bar], str]
    ) -> Result[Bar | None, str]:
        """REQ_F_PAP_002 + REQ_SDD_WEB2_004 — graceful degradation.

        ``Ok(Some(bar))`` ⇒ pass through; clear the degraded flag
        if previously set.
        ``Ok(Nothing())`` ⇒ pass through (no-bar signal).
        ``Err("data:upstream_blocked"|"network:timeout")`` ⇒ fall
        back to the cache; if the cache has a bar, mark the
        runtime as degraded and return that bar; otherwise
        return ``paper:no_cached_data`` so the caller can
        surface the failure.
        Other Err categories propagate unchanged.
        """
        match bar_result:
            case Ok(Some(bar)):
                # Live fetch succeeded; clear any previous
                # degradation banner.
                self._degraded_since = None
                return Ok(bar)
            case Ok(Nothing()):
                return Ok(None)
            case Err(reason) if reason.startswith("data:upstream_blocked") or reason.startswith(
                "network:timeout"
            ):
                # Fall back to the cache.
                cached = self.bar_source.latest_cached()
                match cached:
                    case Ok(Some(bar)):
                        if self._degraded_since is None:
                            self._degraded_since = datetime.now(tz=UTC)
                        return Ok(bar)
                    case Ok(Nothing()):
                        return Err("paper:no_cached_data")
                    case Err(cache_reason):
                        return Err(f"paper:cache_lookup_failed:{cache_reason}")
                return Err("paper:no_cached_data")
            case Err(reason):
                return Err(reason)
        return Err("paper:bar_resolve_unreachable")

    # ------------------------------------------------------------------
    # Engine step
    # ------------------------------------------------------------------

    def _apply_bar(self, bar: Bar) -> Result[Option[EquityPoint], str]:
        """Apply one bar through the engine pieces:
        broker tick → portfolio mark → strategy evaluate →
        proposals (currently logged, not gated; full risk-engine
        wiring is step (b)) → record equity.
        """
        # 1. Convert Bar → Tick + forward to the broker.
        tick: Tick = _bar_to_tick(self.instrument, bar, self.spread_pct)
        self.broker.process_tick(tick)
        self._last_tick_at = tick.at

        # 2. Mark portfolio at the latest price.
        self.portfolio.mark({self.instrument.id: tick.last})

        # 3. Evaluate the strategy.
        if self.market_data_provider is not None:
            state = MarketState(
                at=tick.at,
                portfolio=self.portfolio,
                constraints=self.phase_constraints,
                regime=self.regime,
                screener_ranking=(),
                market=self.market_data_provider,
            )
            _proposals = self.strategy.evaluate(state)
        else:
            # No market data provider wired — v1 paper-trading slice
            # focuses on the broker + portfolio tick loop. Skip
            # strategy evaluation; equity-curve snapshots still
            # record so the dashboard renders. Step 1 (b) lands
            # the strategy + risk wiring with the live yfinance
            # provider in this slot.
            _proposals = []
        # NOTE: full risk-engine gating + broker.submit wiring lands
        # in CR-019 step 1 (b) (REQ_F_WEB2_004 backtest workflow +
        # the per-strategy attribution panel). v1 records equity
        # snapshots so the dashboard renders the live curve;
        # proposals are observed but not executed yet. This keeps
        # the first slice small + deterministic. The deferred wiring
        # is tracked in CR-019's open-question 2.
        del _proposals

        # 4. Record equity point.
        self.portfolio.record_equity(tick.at)
        # Read the freshly-appended point.
        curve = self.portfolio.equity_curve
        if not curve:
            # Portfolio refused to record (e.g., empty pre-tax state).
            return Ok(Nothing())
        latest = curve[-1]
        self._equity_points.append(latest)

        # 5. Persist the equity point (REQ_F_PAP_003).
        if self.equity_repo is not None:
            persist_result = self.equity_repo.append_equity_point(
                latest, account_id=self.session.account_id
            )
            if isinstance(persist_result, Err):
                return Err(f"paper:persist_equity_point:{persist_result.error}")

        return Ok(Some(latest))


# ---------------------------------------------------------------------------
# RuntimeRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RuntimeRegistry:
    """Process-wide registry of paper-trading runtimes
    (REQ_F_PAP_005).

    Holds **at most one live-ticking runtime per account_id**.
    Saved sessions sit in persistence (the CR-008 wiring lands
    in step 1 (b)); v1 keeps the registry in-memory so the
    dashboard surface is testable without a SQLite round-trip.
    """

    _live: dict[AccountId, PaperTradingRuntime] = field(default_factory=dict)

    def start(self, runtime: PaperTradingRuntime) -> Result[None, str]:
        """REQ_F_PAP_005 — duplicate live ticks rejected."""
        if runtime.session.account_id in self._live:
            return Err(f"paper:already_live:{runtime.session.account_id}")
        if not runtime.is_alive():
            return Err(f"paper:session_already_stopped:{runtime.session.account_id}")
        self._live[runtime.session.account_id] = runtime
        return Ok(None)

    def stop(self, account_id: AccountId) -> Result[None, str]:
        runtime = self._live.pop(account_id, None)
        if runtime is None:
            return Err(f"paper:not_live:{account_id}")
        runtime.stop()
        return Ok(None)

    def status(self, account_id: AccountId) -> Option[PaperTradingRuntime]:
        runtime = self._live.get(account_id)
        if runtime is None:
            return Nothing()
        return Some(runtime)

    def live_account_ids(self) -> tuple[AccountId, ...]:
        return tuple(sorted(self._live.keys()))

    def resume_from_persistence(
        self, repo: PortfolioRepository
    ) -> Result[tuple[AccountId, ...], str]:
        """Discover every ``paper-*`` account_id that has at least
        one persisted equity-point row (REQ_F_PAP_003).

        The v1 surface is **discovery only** — the registry does
        NOT auto-revive live ticking because the session metadata
        (universe, strategy_id, instrument) is not yet persisted;
        an operator picks one of the returned ids from the
        recovery wizard and re-supplies the missing inputs to
        rehydrate a runtime. The persisted equity series is the
        durable artefact — the runtime's `.equity_history()` after
        rehydration concatenates the persisted history with new
        ticks (the in-memory accumulator is hydrated from
        `repo.equity_curve(account_id=...)` at rehydration time).

        Returns the discovered account_ids on success;
        ``Err("persistence:...")`` on a repository-layer failure.
        """
        return repo.list_account_ids_with_prefix(PAPER_ACCOUNT_PREFIX)


# ---------------------------------------------------------------------------
# Convenience constructor
# ---------------------------------------------------------------------------


def build_runtime(
    *,
    session: PaperTradingSession,
    instrument: Instrument,
    strategy: Strategy,
    bar_source: BarSource,
    phase_constraints: PhaseConstraints,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
    fee_commission: Money | None = None,
    slippage_seed: int = 0,
) -> Result[PaperTradingRuntime, str]:
    """Wire up a runtime against the documented defaults.

    The broker is a fresh ``LocalBrokerAdapter`` with the
    session's starting capital, a zero-spread/zero-fee model for
    the v1 paper-trading slice (operators override via the
    upcoming config), and a zero-slippage model (CR-019 step
    1 (b) wires the gaussian slippage model).
    """
    fees = fee_commission or Money(
        Decimal("0"), currency=session.starting_capital.currency
    )
    adapter = LocalBrokerAdapter(
        starting_cash=session.starting_capital,
        fee_model=FlatFeeModel(commission=fees, spread_bps=Decimal(0)),
        slippage_model=ZeroSlippageModel(),
        seed=slippage_seed,
    )
    adapter.register_instrument(instrument)
    portfolio = Portfolio.empty(session.starting_capital)
    return Ok(
        PaperTradingRuntime(
            session=session,
            instrument=instrument,
            strategy=strategy,
            bar_source=bar_source,
            broker=adapter,
            portfolio=portfolio,
            phase_constraints=phase_constraints,
            regime=regime,
        )
    )


# Avoid unused-import warnings for symbols kept around for the
# Protocol surface but not directly referenced.
_ = InstrumentId
_ = AllocationBucket
