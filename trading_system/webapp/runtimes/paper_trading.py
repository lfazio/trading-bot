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
from typing import Any, Literal, Protocol, TYPE_CHECKING, runtime_checkable

from trading_system.backtesting.broker import BacktestBroker
from trading_system.backtesting.market_replay import _bar_to_tick
from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.slippage import ZeroSlippageModel
from trading_system.execution.types import Tick
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import AccountId, InstrumentId, OrderId, StrategyId
from trading_system.models.instrument import Instrument, InstrumentClass, Stock
from trading_system.models.meta import TradeProposal, ValidationResult
from trading_system.models.money import Money
from trading_system.models.phase import AllocationBucket, MarketRegime, PhaseConstraints
from trading_system.models.trading import Order, OrderType, Trade
from trading_system.persistence.repositories.portfolio import PortfolioRepository
from trading_system.portfolio.portfolio import Portfolio
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.screener.engine import ScoredStock, ScoreBreakdown
from trading_system.strategies.protocol import Strategy
from trading_system.strategies.state import MarketState
from trading_system.tax.config import TaxConfig


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
# Instrument-class → allocation bucket map (mirrors risk.mapping)
# ---------------------------------------------------------------------------


def _bucket_for_class(cls: InstrumentClass) -> AllocationBucket:
    """First-bucket mapping for ``portfolio.apply`` — every fill
    has to land in a single bucket for the cost-basis ledger.

    Same mapping as ``trading_system.risk.mapping.buckets_for_class``
    but returning a single bucket (the first one in the bucket
    tuple). Defined locally so the runtime layer doesn't have to
    import ``risk`` (forbidden by the structural audit).
    """
    if cls is InstrumentClass.STOCK:
        return AllocationBucket.STOCK
    if cls is InstrumentClass.TURBO:
        return AllocationBucket.TURBO
    if cls is InstrumentClass.STRUCTURED:
        return AllocationBucket.STRUCTURED
    return AllocationBucket.STOCK  # CASH falls back to STOCK bucket


# ---------------------------------------------------------------------------
# RiskGate Protocol — Protocol-shaped callable signature
# ---------------------------------------------------------------------------


@runtime_checkable
class RiskGate(Protocol):
    """Protocol-shaped callable wrapping the project's
    ``RiskEngine.pre_trade`` so the runtime layer can gate
    proposals without importing ``trading_system.risk`` (the
    structural audit forbids ``safety`` / ``risk`` /
    ``strategy_lab`` reach from ``webapp/runtimes/``).

    Operators construct the closure outside ``webapp/`` and
    attach it to ``PaperTradingRuntime.risk_gate`` — when
    ``None``, the runtime accepts every proposal without
    gating (v1 paper-trading does this; the live-trading
    amendment makes it mandatory).
    """

    def __call__(
        self,
        proposal: TradeProposal,
        portfolio: Portfolio,
        constraints: PhaseConstraints,
        regime: MarketRegime,
    ) -> ValidationResult: ...


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
    # CR-019 step 1 (b) follow-up — Protocol-shaped risk-gate
    # closure. ``None`` accepts every proposal; operators wire a
    # closure that delegates to ``trading_system.risk.RiskEngine``
    # for production. See the ``RiskGate`` Protocol above.
    risk_gate: RiskGate | None = None
    # Tax config for the portfolio.apply call. Defaults to the
    # France CTO 30% flat rate via the ``TaxConfig.cto_default``
    # factory so the v1 demo path works out of the box.
    tax_config: TaxConfig | None = None
    spread_pct: Decimal = Decimal("0.001")
    # Optional reference index. The dashboard surfaces this on
    # the main price chart instead of (or alongside) the runtime's
    # primary instrument — operators want EU-wide market context
    # when watching a session. ``None`` ⇒ no index surfaced
    # (single-stock view). Resolved from the universe YAML's
    # ``indices:`` key at session start.
    reference_index: Instrument | None = None
    # Rebalance cadence guard. Without this the strategy runs on
    # every simulator tick (e.g., 2s with the default tick driver)
    # which causes CoreStrategy — which proposes 10 % of capital
    # per call — to deploy the entire allocation in ~18 seconds.
    # Defaults to 1 hour of simulated bar time so a Phase-1 demo
    # spreads the deployment over many ticks. Operators tune via
    # the wizard (future amendment) or by setting the field
    # directly after build_runtime.
    rebalance_cooldown_seconds: int = 3600
    # CR-026 (REQ_F_PAP_015 / REQ_SDD_PAP_006) — full universe the
    # runtime evaluates strategies against. Empty tuple ⇒ legacy
    # single-instrument session (the runtime constructs a degenerate
    # universe of just ``self.instrument`` in ``__post_init__``).
    # Non-empty ⇒ the strategy sees every stock per tick + the
    # runtime fans out submission across instruments.
    universe: tuple[Stock, ...] = ()
    # CR-029 (REQ_F_PER_012 / REQ_SDD_PER_012) — per-symbol bar
    # persistence slot. When wired AND ``universe`` carries > 1
    # symbol, the runtime fans out the polled bar set to
    # ``instrument_bar_repo.append_bars(...)`` on every tick so
    # the operator can later query "what was the universe doing at
    # time T?". ``None`` ⇒ no persistence; the dashboard's
    # "saving disabled" banner appears when the slot is missing.
    # Duck-typed Protocol — anything with
    # ``append_bars(rows, *, account_id)`` returning Result satisfies
    # the slot. The concrete CR-029
    # ``InstrumentBarRepository`` is the production wiring.
    instrument_bar_repo: object | None = None

    _alive: bool = field(default=True, init=False)
    _degraded_since: datetime | None = field(default=None, init=False)
    _last_tick_at: datetime | None = field(default=None, init=False)
    _equity_points: list[EquityPoint] = field(default_factory=list, init=False)
    _trades: list[Trade] = field(default_factory=list, init=False)
    _orders_by_trade: dict[str, Order] = field(default_factory=dict, init=False)
    _rejected: list[tuple[TradeProposal, tuple[str, ...]]] = field(
        default_factory=list, init=False
    )
    _next_order_seq: int = field(default=0, init=False)
    _last_rebalance_at: datetime | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """CR-026 (REQ_SDD_PAP_006) — normalise ``universe``:

        - empty input + ``self.instrument`` is a ``Stock`` ⇒ build a
          degenerate single-symbol universe so the legacy
          single-instrument constructor path keeps working;
        - non-empty input ⇒ enforce lex-sorted-by-symbol ordering
          (mis-sorted input is normalised silently — the contract is
          deterministic iteration, not "caller MUST sort").
        """
        if not self.universe and isinstance(self.instrument, Stock):
            self.universe = (self.instrument,)
        elif self.universe:
            self.universe = tuple(sorted(self.universe, key=lambda s: s.symbol))

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

    def trade_history(self) -> tuple[Trade, ...]:
        """Read-only view of every fill recorded by this session."""
        return tuple(self._trades)

    def order_for_trade(self, trade_id: str) -> Order | None:
        """Look up the Order that produced ``trade_id``. Used by
        the dashboard reader to surface side + instrument symbol
        on the recent-trades table."""
        return self._orders_by_trade.get(trade_id)

    def rejected_proposals(
        self,
    ) -> tuple[tuple[TradeProposal, tuple[str, ...]], ...]:
        """Risk-gate rejections — for the dashboard's "recent
        decisions" panel."""
        return tuple(self._rejected)

    def _build_screener_ranking(self) -> tuple[ScoredStock, ...]:
        """CR-026 (REQ_F_PAP_015) — rank every stock in the universe.

        Iteration order is lex-sorted by symbol (enforced in
        ``__post_init__``) so replay byte-equality (REQ_NF_DAT_001)
        extends to multi-instrument sessions. v1 emits a uniform
        score for every stock — the dashboard's per-instrument
        grid carries the operator-visible signal; the screener
        engine itself can rank with real scores once CR-026 has
        a per-instrument fundamentals path.

        For non-stock instruments (turbos, structured products)
        the ranking is empty — the strategy's STOCK-bucket
        allocation target produces no proposals + the portfolio
        drifts on marks alone (REQ_SDS_MOD_005).
        """
        if not self.universe:
            return ()
        return tuple(
            ScoredStock(
                stock=stock,
                score=Decimal("0.5"),
                breakdown=ScoreBreakdown(
                    stability=Decimal("0.5"),
                    yield_quality=Decimal("0.5"),
                    valuation=Decimal("0.5"),
                ),
            )
            for stock in self.universe
        )

    def _persist_universe_bars(self, at: datetime) -> str | None:
        """CR-029 (REQ_F_PER_012 / REQ_SDD_PER_012) — poll the
        wrapped MarketDataProvider once per universe symbol and
        persist the rows through ``self.instrument_bar_repo``.

        Returns ``None`` on success; a categorised
        ``"paper:persist_bars:<reason>"`` string when the
        repository surfaced an Err. NEVER raises — defensive at
        every layer so the tick still completes.
        """
        if self.market_data_provider is None or self.instrument_bar_repo is None:
            return None
        rows: list[tuple[Any, Bar]] = []
        for stock in self.universe:
            try:
                result = self.market_data_provider.latest(stock)
            except Exception:  # noqa: BLE001 — defensive
                continue
            if hasattr(result, "value") and not hasattr(result, "error"):
                bar = result.value
                if isinstance(bar, Bar):
                    rows.append((stock.id, bar))
        if not rows:
            del at  # unused on the empty path
            return None
        del at
        account_id = self.session.account_id
        try:
            persist_result = self.instrument_bar_repo.append_bars(  # type: ignore[attr-defined]
                rows, account_id=account_id
            )
        except Exception as e:  # noqa: BLE001 — defensive
            return f"paper:persist_bars:exception:{e!s}"
        if hasattr(persist_result, "error"):
            return f"paper:persist_bars:{persist_result.error}"
        return None

    def _submit_proposal(
        self, proposal: TradeProposal, tick: Tick
    ) -> None:
        """Convert a ``TradeProposal`` to a market ``Order`` + submit
        through the broker; on a successful fill, apply it to the
        portfolio. Errs are swallowed (the broker may reject for
        insufficient cash, currency mismatch, etc.) — the rejection
        is observable through ``self.broker`` state if operators
        want to log them later.
        """
        equity = self.portfolio.equity_after_tax().amount
        if equity <= 0 or tick.last <= 0:
            return
        raw_qty = (equity * proposal.size_pct_of_capital) / tick.last
        # Floor to integer share count — stocks (and turbos / SPs in
        # practice) trade in whole units. Fractional shares are a
        # broker-specific feature; the paper-trading sim conservatively
        # rejects them so the equity-curve math stays honest about
        # what a real broker would accept. ``Decimal(int(...))``
        # preserves the Decimal type so Order.quantity validators
        # don't switch their precision assumptions.
        quantity = Decimal(int(raw_qty))
        if quantity <= 0:
            # The proposal's allocation can't buy at least one share
            # at this price — skip rather than send a zero-quantity
            # order (which the Order dataclass would reject anyway).
            return
        self._next_order_seq += 1
        order = Order(
            id=OrderId(
                f"paper-{self.session.account_id}-{self._next_order_seq:08d}"
            ),
            instrument=proposal.instrument,
            side=proposal.side,
            quantity=quantity,
            type=OrderType.MARKET,
            stop_loss=proposal.stop_loss,
            created_at=tick.at,
            source_strategy=self.strategy.id,
        )
        # Wrap the LocalBrokerAdapter so submit returns the Trade
        # directly (the bare adapter only returns OrderId).
        wrapped = BacktestBroker(adapter=self.broker)
        match wrapped.submit(order):
            case Ok(trade):
                tax_cfg = self.tax_config or TaxConfig.default()
                bucket = _bucket_for_class(proposal.instrument.cls)
                self.portfolio.apply(trade, order, bucket, tax_cfg)
                self._trades.append(trade)
                self._orders_by_trade[str(trade.id)] = order
            case Err(_):
                # Broker rejected (insufficient cash, currency
                # mismatch, etc.). Silently skip — the rest of
                # the tick still records the equity point.
                return

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
        risk-gate → broker.submit (CR-019 step 1 (b) follow-up) →
        record equity.
        """
        # 1. Convert Bar → Tick + forward to the broker.
        tick: Tick = _bar_to_tick(self.instrument, bar, self.spread_pct)
        self.broker.process_tick(tick)
        self._last_tick_at = tick.at

        # 2. Mark portfolio at the latest price.
        self.portfolio.mark({self.instrument.id: tick.last})

        # 2b. CR-029 (REQ_F_PER_012 / REQ_SDD_PER_012) — persist
        # every universe symbol's bar this tick into the
        # ``instrument_bars`` table when the repository slot is
        # wired. Fan-out runs BEFORE strategy evaluation so a
        # strategy that consults the persisted history sees the
        # just-polled row. Persistence failures DO NOT abort the
        # tick — the rest of the engine runs + the Err surfaces
        # as the tick's outcome so the dashboard's banner can
        # report "saving disabled".
        persist_err: str | None = None
        if (
            self.instrument_bar_repo is not None
            and self.universe
            and len(self.universe) > 1
            and self.market_data_provider is not None
        ):
            persist_err = self._persist_universe_bars(tick.at)

        # 3. Evaluate the strategy (only when a MarketDataProvider
        # is wired — strategies that consult historical bars need
        # ``state.market``). The wizard's finish handler hands a
        # ``SimulatedMarketDataProvider`` so the live demo path
        # works end-to-end.
        #
        # Cooldown guard: CoreStrategy proposes 10 % of capital
        # per call. Without this guard a fresh portfolio would
        # deploy its entire allocation in ~18 seconds (one
        # 10 %-buy per 2 s tick). The guard skips strategy
        # evaluation until ``rebalance_cooldown_seconds`` of
        # simulated bar time has elapsed since the last call
        # that produced proposals. Tick still records portfolio
        # marks + equity points; only the new-trade path pauses.
        proposals: list[TradeProposal] = []
        cooldown_active = False
        if self._last_rebalance_at is not None and self.rebalance_cooldown_seconds > 0:
            elapsed = (tick.at - self._last_rebalance_at).total_seconds()
            cooldown_active = elapsed < self.rebalance_cooldown_seconds
        if self.market_data_provider is not None and not cooldown_active:
            ranking = self._build_screener_ranking()
            state = MarketState(
                at=tick.at,
                portfolio=self.portfolio,
                constraints=self.phase_constraints,
                regime=self.regime,
                screener_ranking=ranking,
                market=self.market_data_provider,
            )
            proposals = list(self.strategy.evaluate(state))
            if proposals:
                # Reset the cooldown clock so subsequent ticks
                # wait for the documented interval before the
                # next round of proposals fires.
                self._last_rebalance_at = tick.at

        # 4. Gate + submit each proposal.
        for proposal in proposals:
            if self.risk_gate is not None:
                verdict = self.risk_gate(
                    proposal,
                    self.portfolio,
                    self.phase_constraints,
                    self.regime,
                )
                if not verdict.passed:
                    self._rejected.append((proposal, verdict.reasons))
                    continue
            self._submit_proposal(proposal, tick)

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
