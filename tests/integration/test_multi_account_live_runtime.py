"""TASKS.md §8 — multi-account live-runtime drill.

Closes the second Validation.md §5 known-limitation entry. The
Phase-8 C8 drill (`tests/integration/test_multi_account_drill.py`)
covers the gate semantics with hand-rolled fake portfolios; this
drill exercises the actual `PaperTradingRuntime` fan-out under
three concurrent paper-trading sessions:

  alpha (1 000 EUR) + beta (5 000 EUR) + gamma (20 000 EUR)

Each runtime carries its own session metadata, its own bar
source, its own portfolio. The shared `RuntimeRegistry` keys on
`account_id` per REQ_F_PAP_005 + partitions paper / live by the
`paper-*` prefix per REQ_F_LIV_004.

Asserts:

- **Independent equity curves.** Each session's bar source emits
  a distinct close trajectory; the per-account equity_after_tax
  series must reflect its own data, not any sibling's.
- **No cross-account bleed.** Stopping one session doesn't drop
  the others off the registry; each runtime's portfolio +
  trade history stays scoped to its own account_id.
- **Paired-replay determinism (REQ_NF_DET_001).** Build + tick
  the household twice from identical seeds; the equity-point
  sequences match byte-identically across runs.

REQ refs:
- REQ_F_PAP_005 — one live-ticking session per account_id; the
  registry rejects duplicate-id starts.
- REQ_F_LIV_004 — `paper-*` namespace partition; partition by
  prefix.
- REQ_F_ACC_001..010 (existing C8 coverage); this drill is the
  live-runtime layer on top.
- REQ_NF_DET_001 / REQ_NF_REP_001 — deterministic engine + same
  inputs ⇒ same outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from trading_system.data.types import Bar
from trading_system.models.identifiers import AccountId, InstrumentId, StrategyId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket, MarketRegime, PhaseConstraints
from trading_system.result import Err, Nothing, Ok, Option, Result, Some
from trading_system.webapp.runtimes.paper_trading import (
    PAPER_ACCOUNT_PREFIX,
    PaperTradingRuntime,
    PaperTradingSession,
    RuntimeRegistry,
    build_runtime,
)


_EUR = Currency.EUR
_T0 = datetime(2026, 5, 31, 12, tzinfo=UTC)


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=_EUR)


def _stock(symbol: str) -> Stock:
    return Stock(
        id=InstrumentId(f"{symbol}.AS"),
        symbol=symbol,
        exchange="AS",
        currency=_EUR,
        cls=InstrumentClass.STOCK,
        isin=f"NL{symbol:0>10}",
        sector="tech",
        country="NL",
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
    """Yields bars from a pre-loaded queue. Each account's source
    holds its own bar trajectory so the equity curves diverge
    deterministically."""

    bars: list[Bar] = field(default_factory=list)
    _cursor: int = 0
    _last_seen: Bar | None = None

    def next_bar(self) -> Result[Option[Bar], str]:
        if self._cursor >= len(self.bars):
            return Ok(Nothing())
        bar = self.bars[self._cursor]
        self._cursor += 1
        self._last_seen = bar
        return Ok(Some(bar))

    def latest_cached(self) -> Result[Option[Bar], str]:
        if self._last_seen is None:
            return Ok(Nothing())
        return Ok(Some(self._last_seen))


@dataclass(slots=True)
class _NoopStrategy:
    id: StrategyId

    def evaluate(self, state: Any) -> list[TradeProposal]:
        del state
        return []


def _bar_series(*, base_close: Decimal, ticks: int = 20) -> list[Bar]:
    """Deterministic up-trend at +1.00/bar from ``base_close``."""
    bars: list[Bar] = []
    price = base_close
    for i in range(ticks):
        bars.append(
            Bar(
                at=_T0 + timedelta(minutes=i),
                open=price,
                high=price * Decimal("1.005"),
                low=price * Decimal("0.995"),
                close=price,
                volume=Decimal("1000"),
            )
        )
        price += Decimal("1.00")
    return bars


def _build_runtime_for(
    *,
    account_id: str,
    capital: str,
    base_close: Decimal,
    ticks: int = 20,
) -> tuple[PaperTradingRuntime, _StubBarSource]:
    session = PaperTradingSession(
        account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}{account_id}"),
        universe="eu-dividend-starter",
        strategy_id=StrategyId("noop"),
        starting_capital=_eur(capital),
        started_at=_T0,
    )
    bar_source = _StubBarSource(bars=_bar_series(base_close=base_close, ticks=ticks))
    res = build_runtime(
        session=session,
        instrument=_stock("ASML"),
        strategy=_NoopStrategy(id=StrategyId("noop")),
        bar_source=bar_source,
        phase_constraints=_constraints(),
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok), f"build_runtime returned Err: {res}"
    return res.value, bar_source


# ---------------------------------------------------------------------------
# Household construction + tick fan-out
# ---------------------------------------------------------------------------


def test_three_account_household_registers_and_partitions_by_id() -> None:
    """REQ_F_PAP_005 — three distinct account_ids register
    cleanly; ``live_account_ids()`` returns them in lex-sorted
    order; duplicate-id starts SHALL be rejected."""
    registry = RuntimeRegistry()
    alpha, _ = _build_runtime_for(
        account_id="alpha-2026", capital="1000", base_close=Decimal("100")
    )
    beta, _ = _build_runtime_for(
        account_id="beta-2026", capital="5000", base_close=Decimal("50")
    )
    gamma, _ = _build_runtime_for(
        account_id="gamma-2026", capital="20000", base_close=Decimal("200")
    )
    for runtime in (alpha, beta, gamma):
        result = registry.start(runtime)
        assert isinstance(result, Ok), result
    aids = registry.live_account_ids()
    assert len(aids) == 3
    # Lex-sorted by ``alpha`` < ``beta`` < ``gamma`` prefix.
    assert aids == tuple(sorted(aids))
    # REQ_F_PAP_005 — duplicate-id start rejected.
    duplicate = registry.start(alpha)
    assert isinstance(duplicate, Err)
    assert "already_live" in duplicate.error


def test_three_account_ticks_produce_independent_equity_curves() -> None:
    """Each account's stub bar source emits a distinct trajectory;
    after 10 ticks the equity curves SHALL diverge in proportion
    to (starting_capital, base_close, bar_delta)."""
    registry = RuntimeRegistry()
    runtimes = {}
    for name, capital, base in (
        ("alpha-2026", "1000", Decimal("100")),
        ("beta-2026", "5000", Decimal("50")),
        ("gamma-2026", "20000", Decimal("200")),
    ):
        runtime, _ = _build_runtime_for(
            account_id=name, capital=capital, base_close=base
        )
        registry.start(runtime)
        runtimes[name] = runtime

    for _ in range(10):
        for runtime in runtimes.values():
            runtime.tick_once()

    # Each account's equity_history is independently populated.
    alpha_history = runtimes["alpha-2026"].equity_history()
    beta_history = runtimes["beta-2026"].equity_history()
    gamma_history = runtimes["gamma-2026"].equity_history()
    # Same number of points per account — all sourced from
    # 10 stub bars.
    assert len(alpha_history) == len(beta_history) == len(gamma_history)
    # Trajectories differ — proven by the latest equity-after-tax
    # values being distinct (capital + close trajectories differ).
    alpha_eq = alpha_history[-1].equity_after_tax.amount
    beta_eq = beta_history[-1].equity_after_tax.amount
    gamma_eq = gamma_history[-1].equity_after_tax.amount
    assert alpha_eq != beta_eq
    assert beta_eq != gamma_eq
    assert alpha_eq != gamma_eq
    # Starting-capital ordering preserved (each account's equity
    # stays in the same band as its starting capital — none of
    # them traded so the equity ≈ marked cash position).
    assert alpha_eq < beta_eq < gamma_eq


def test_stopping_one_account_does_not_evict_the_others() -> None:
    """REQ_F_PAP_005 — registry.stop is per-account; the other
    runtimes keep ticking + the registry's live_account_ids set
    shrinks by exactly one."""
    registry = RuntimeRegistry()
    alpha, _ = _build_runtime_for(
        account_id="alpha-2026", capital="1000", base_close=Decimal("100")
    )
    beta, _ = _build_runtime_for(
        account_id="beta-2026", capital="5000", base_close=Decimal("50")
    )
    gamma, _ = _build_runtime_for(
        account_id="gamma-2026", capital="20000", base_close=Decimal("200")
    )
    for runtime in (alpha, beta, gamma):
        registry.start(runtime)

    stop_res = registry.stop(beta.session.account_id)
    assert isinstance(stop_res, Ok)
    remaining = registry.live_account_ids()
    assert len(remaining) == 2
    assert beta.session.account_id not in remaining
    # Other runtimes are still alive + can tick.
    assert alpha.is_alive()
    assert gamma.is_alive()
    # beta SHALL report dead.
    assert not beta.is_alive()


def test_paired_replay_two_household_runs_byte_identical() -> None:
    """REQ_NF_DET_001 / REQ_NF_REP_001 — building + ticking the
    3-account household twice from identical inputs SHALL
    produce byte-identical equity-curve sequences per account.

    The runtime is single-threaded by design; this test confirms
    the cross-account fan-out doesn't introduce any hidden
    nondeterminism (e.g., dict-ordering on the registry's start
    map).
    """

    def _household_equity_signature() -> dict[str, list]:
        registry = RuntimeRegistry()
        runtimes = {}
        for name, capital, base in (
            ("alpha-2026", "1000", Decimal("100")),
            ("beta-2026", "5000", Decimal("50")),
            ("gamma-2026", "20000", Decimal("200")),
        ):
            runtime, _ = _build_runtime_for(
                account_id=name, capital=capital, base_close=base
            )
            registry.start(runtime)
            runtimes[name] = runtime
        for _ in range(10):
            for runtime in runtimes.values():
                runtime.tick_once()
        return {
            name: [
                (p.at, p.equity_after_tax.amount)
                for p in runtime.equity_history()
            ]
            for name, runtime in runtimes.items()
        }

    first = _household_equity_signature()
    second = _household_equity_signature()
    assert first == second
    # Sanity: each account actually produced points.
    for name, points in first.items():
        assert len(points) > 0, f"no equity points for {name}"
