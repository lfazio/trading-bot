"""Tests for ``trading_system.webapp.runtimes.paper_trading``.

CR-019 step 1 (a) — paper-trading runtime mode.

REQ refs verified:
- REQ_F_PAP_001 / REQ_SDS_WEB2_004 / REQ_SDD_WEB2_003 —
  runtime composes LocalBrokerAdapter + portfolio + BarSource;
  `tick_once` is the unit of work.
- REQ_F_PAP_002 / REQ_SDD_WEB2_004 — yfinance graceful
  degradation to cached-only mode with a degraded banner.
- REQ_F_PAP_004 — `paper-<utc-iso-timestamp>` account_id
  namespace.
- REQ_F_PAP_005 — one live-ticking session per account_id at a
  time; duplicate `start` returns `paper:already_live:<id>`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from trading_system.data.types import Bar
from trading_system.models.identifiers import AccountId, InstrumentId, StrategyId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket, MarketRegime, PhaseConstraints
from trading_system.models.meta import TradeProposal
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.webapp.runtimes.paper_trading import (
    PAPER_ACCOUNT_PREFIX,
    PaperTradingRuntime,
    PaperTradingSession,
    RuntimeRegistry,
    build_runtime,
    new_paper_account_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_EUR = Currency.EUR
_T0 = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=_EUR)


def _stock(symbol: str = "ASML") -> Stock:
    return Stock(
        id=InstrumentId(f"{symbol}.AS"),
        symbol=symbol,
        exchange="AS",
        currency=_EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _bar(*, close: str, day: int) -> Bar:
    p = Decimal(close)
    return Bar(
        at=_T0 + timedelta(days=day),
        open=p,
        high=p * Decimal("1.005"),
        low=p * Decimal("0.995"),
        close=p,
        volume=Decimal("1000"),
    )


def _constraints() -> PhaseConstraints:
    return PhaseConstraints(
        max_positions=3,
        max_trades_per_month=4,
        allocation_targets={
            AllocationBucket.STOCK: Decimal("0.90"),
            AllocationBucket.TACTICAL: Decimal("0.10"),
        },
        turbo_exposure_max=Decimal("0"),
        risk_per_trade_band=(Decimal("0.01"), Decimal("0.02")),
        max_drawdown=Decimal("0.15"),
    )


@dataclass(slots=True)
class _StubBarSource:
    """Yields bars from a pre-loaded queue; falls back to the
    last seen bar on ``latest_cached``. Tests inject Errs by
    putting an ``Err`` instance in the queue."""

    bars: list[Bar | Err[Any]] = field(default_factory=list)
    _cursor: int = 0
    _last_seen: Bar | None = None
    no_new_after: int | None = None  # if cursor passes this, return Ok(Nothing)

    def next_bar(self) -> Result[Option[Bar], str]:
        if self.no_new_after is not None and self._cursor >= self.no_new_after:
            return Ok(Nothing())
        if self._cursor >= len(self.bars):
            return Ok(Nothing())
        item = self.bars[self._cursor]
        self._cursor += 1
        if isinstance(item, Err):
            return item
        self._last_seen = item
        return Ok(Some(item))

    def latest_cached(self) -> Result[Option[Bar], str]:
        if self._last_seen is None:
            return Ok(Nothing())
        return Ok(Some(self._last_seen))


@dataclass(slots=True)
class _NoopStrategy:
    """Emits no proposals; lets us focus on the tick + equity loop."""

    id: StrategyId

    def evaluate(self, state: Any) -> list[TradeProposal]:
        del state
        return []


def _session() -> PaperTradingSession:
    return PaperTradingSession(
        account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-22T12:00:00+00:00"),
        universe="eu-dividend-starter",
        strategy_id=StrategyId("noop"),
        starting_capital=_eur("10000"),
        started_at=_T0,
    )


def _build(
    *,
    bars: list[Bar | Err[Any]] | None = None,
    no_new_after: int | None = None,
) -> tuple[PaperTradingRuntime, _StubBarSource]:
    bar_source = _StubBarSource(bars=bars or [], no_new_after=no_new_after)
    res = build_runtime(
        session=_session(),
        instrument=_stock(),
        strategy=_NoopStrategy(id=StrategyId("noop")),
        bar_source=bar_source,
        phase_constraints=_constraints(),
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok), f"build_runtime returned Err: {res}"
    return res.value, bar_source


# ---------------------------------------------------------------------------
# Session invariants
# ---------------------------------------------------------------------------


def test_session_rejects_account_id_without_paper_prefix() -> None:
    """REQ_F_PAP_004 — account_id SHALL start with ``paper-``."""
    with pytest.raises(ValueError, match="paper-"):
        PaperTradingSession(
            account_id=AccountId("default"),  # missing prefix
            universe="x",
            strategy_id=StrategyId("noop"),
            starting_capital=_eur("10000"),
            started_at=_T0,
        )


def test_session_rejects_non_positive_capital() -> None:
    with pytest.raises(ValueError, match="starting_capital"):
        PaperTradingSession(
            account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-22T00:00:00+00:00"),
            universe="x",
            strategy_id=StrategyId("noop"),
            starting_capital=_eur("0"),
            started_at=_T0,
        )


def test_session_mode_tag_is_locked_to_paper() -> None:
    s = _session()
    assert s.mode_tag == "paper"


def test_new_paper_account_id_uses_prefix() -> None:
    fixed = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    aid = new_paper_account_id(now=lambda: fixed)
    assert aid.startswith(PAPER_ACCOUNT_PREFIX)
    assert "2026-05-22T12:00:00+00:00" in aid


# ---------------------------------------------------------------------------
# Tick loop happy path
# ---------------------------------------------------------------------------


def test_tick_once_consumes_a_bar_and_records_equity() -> None:
    runtime, _ = _build(bars=[_bar(close="100", day=0)])
    result = runtime.tick_once()
    assert isinstance(result, Ok)
    inner = result.value
    assert isinstance(inner, Some)
    assert runtime.last_tick_at() == _T0
    # One equity point on the curve.
    assert len(runtime.equity_history()) == 1
    point = runtime.equity_history()[0]
    assert point.at == _T0


def test_tick_once_returns_nothing_when_no_new_bar() -> None:
    runtime, _ = _build(bars=[], no_new_after=0)
    result = runtime.tick_once()
    assert isinstance(result, Ok)
    assert isinstance(result.value, Nothing)


def test_tick_once_returns_err_after_stop() -> None:
    """REQ_SDS_WEB2_004 — ``stop`` makes ``tick_once`` Err."""
    runtime, _ = _build(bars=[_bar(close="100", day=0)])
    runtime.stop()
    assert runtime.is_alive() is False
    result = runtime.tick_once()
    assert isinstance(result, Err)
    assert result.error == "paper:session_stopped"


def test_multiple_ticks_grow_the_equity_curve() -> None:
    bars = [
        _bar(close="100", day=0),
        _bar(close="101", day=1),
        _bar(close="102", day=2),
    ]
    runtime, _ = _build(bars=bars)
    for _ in bars:
        result = runtime.tick_once()
        assert isinstance(result, Ok)
    assert len(runtime.equity_history()) == 3


# ---------------------------------------------------------------------------
# Graceful degradation — REQ_F_PAP_002 / REQ_SDD_WEB2_004
# ---------------------------------------------------------------------------


def test_upstream_block_falls_back_to_cached_bar() -> None:
    """REQ_F_PAP_002 — yfinance upstream-block Err falls back to
    the cache and marks the runtime degraded."""
    bars = [
        _bar(close="100", day=0),  # primes the cache
        Err("data:upstream_blocked: rate limited"),
    ]
    runtime, _ = _build(bars=bars)
    first = runtime.tick_once()
    assert isinstance(first, Ok) and isinstance(first.value, Some)
    assert runtime.is_degraded() is False

    second = runtime.tick_once()
    assert isinstance(second, Ok) and isinstance(second.value, Some), (
        f"expected fallback bar, got {second!r}"
    )
    assert runtime.is_degraded() is True
    assert runtime.degraded_since() is not None


def test_network_timeout_falls_back_to_cached_bar() -> None:
    """REQ_F_PAP_002 — network:timeout shares the same
    degradation path."""
    bars = [
        _bar(close="100", day=0),
        Err("network:timeout"),
    ]
    runtime, _ = _build(bars=bars)
    runtime.tick_once()
    second = runtime.tick_once()
    assert isinstance(second, Ok)
    assert runtime.is_degraded() is True


def test_recovery_clears_degraded_banner() -> None:
    """REQ_F_PAP_002 — a subsequent successful live fetch SHALL
    clear ``is_degraded`` (operator sees yfinance is back)."""
    bars = [
        _bar(close="100", day=0),
        Err("data:upstream_blocked"),
        _bar(close="101", day=2),
    ]
    runtime, _ = _build(bars=bars)
    runtime.tick_once()
    runtime.tick_once()
    assert runtime.is_degraded() is True
    runtime.tick_once()
    assert runtime.is_degraded() is False


def test_no_cached_data_returns_categorised_err() -> None:
    """REQ_SDD_WEB2_004 — upstream block + empty cache SHALL
    return ``paper:no_cached_data`` so the caller can surface
    the failure."""
    bars = [Err("data:upstream_blocked: no historical fetch ever succeeded")]
    runtime, _ = _build(bars=bars)
    result = runtime.tick_once()
    assert isinstance(result, Err)
    assert result.error == "paper:no_cached_data"


def test_unknown_err_propagates_unchanged() -> None:
    """Non-network Errs SHALL propagate untouched so the caller
    can diagnose."""
    runtime, _ = _build(bars=[Err("data:bad_symbol:NOPE.XX")])
    result = runtime.tick_once()
    assert isinstance(result, Err)
    assert result.error == "data:bad_symbol:NOPE.XX"


# ---------------------------------------------------------------------------
# RuntimeRegistry — REQ_F_PAP_005
# ---------------------------------------------------------------------------


def test_registry_start_then_status_then_stop() -> None:
    runtime, _ = _build(bars=[_bar(close="100", day=0)])
    reg = RuntimeRegistry()
    assert isinstance(reg.start(runtime), Ok)
    status = reg.status(runtime.session.account_id)
    assert isinstance(status, Some)
    assert reg.live_account_ids() == (runtime.session.account_id,)
    assert isinstance(reg.stop(runtime.session.account_id), Ok)
    assert isinstance(reg.status(runtime.session.account_id), Nothing)


def test_registry_rejects_duplicate_live_start() -> None:
    """REQ_F_PAP_005 — at most one live-ticking runtime per
    account_id."""
    runtime, _ = _build(bars=[_bar(close="100", day=0)])
    reg = RuntimeRegistry()
    reg.start(runtime).unwrap()
    second = reg.start(runtime)
    assert isinstance(second, Err)
    assert second.error.startswith("paper:already_live:")
    assert runtime.session.account_id in second.error


def test_registry_stop_unknown_account_returns_err() -> None:
    reg = RuntimeRegistry()
    result = reg.stop(AccountId(f"{PAPER_ACCOUNT_PREFIX}nope"))
    assert isinstance(result, Err)
    assert result.error.startswith("paper:not_live:")


def test_registry_resume_from_persistence_returns_zero_in_v1() -> None:
    """v1 stub — the persistence-wired version lands in step 1 (b)."""
    reg = RuntimeRegistry()
    assert reg.resume_from_persistence() == 0


def test_registry_rejects_already_stopped_runtime() -> None:
    runtime, _ = _build(bars=[_bar(close="100", day=0)])
    runtime.stop()
    reg = RuntimeRegistry()
    result = reg.start(runtime)
    assert isinstance(result, Err)
    assert result.error.startswith("paper:session_already_stopped:")
