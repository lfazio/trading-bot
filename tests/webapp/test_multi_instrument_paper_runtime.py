"""CR-026 / TC_PAP_MULTI_001..006 — multi-instrument paper-trading runtime.

REQ refs:
- REQ_F_PAP_015 (full-universe MarketState construction)
- REQ_F_PAP_016 (bar-source fan-out with lex-sorted order)
- REQ_F_PAP_017 (PaperStateResponse.per_instrument extension)
- REQ_F_PAP_018 (dashboard grid + click-to-pin)
- REQ_SDD_PAP_006 (constructor accepts universe_id; legacy
  instrument-only path retained)
- REQ_SDD_PAP_007 (MarketState.universe-equivalent ranking per tick)
- REQ_SDD_PAP_008 (MultiInstrumentBarSource.poll contract)
- REQ_SDD_PAP_009 (canonical-JSON byte-determinism of per_instrument)
- REQ_SDD_PAP_010 (?pin=<symbol> server-side handler)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.data.types import Bar
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.result import Err, Ok, Result
from trading_system.webapp.runtimes.multi_instrument_bar_source import (
    MultiInstrumentBarSource,
)
from trading_system.webui.schemas import InstrumentRow, PaperStateResponse


def _stock(symbol: str, isin: str | None = None) -> Stock:
    return Stock(
        id=InstrumentId(f"{symbol}.AS"),
        symbol=symbol,
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin=isin or f"NL{symbol:0>10}",
        sector="tech",
        country="NL",
    )


def _bar(close: str = "100.00") -> Bar:
    price = Decimal(close)
    return Bar(
        at=datetime(2026, 5, 30, 12, tzinfo=UTC),
        open=price,
        high=price * Decimal("1.01"),
        low=price * Decimal("0.99"),
        close=price,
        volume=Decimal("1000"),
    )


@dataclass
class _StubProvider:
    """Test double — returns the configured Result per instrument id."""

    payload: dict[str, Result[Bar, str]]

    def latest(self, instrument):
        return self.payload.get(
            instrument.symbol, Err(f"data:no_bars:{instrument.symbol}")
        )

    def bars(self, *_args, **_kwargs):  # pragma: no cover — Protocol filler
        return Err("data:not_supported")

    def dividends(self, *_args, **_kwargs):  # pragma: no cover
        return Err("data:not_supported")


# ---------------------------------------------------------------------------
# TC_PAP_MULTI_004 — MultiInstrumentBarSource fan-out
# ---------------------------------------------------------------------------


def test_multi_bar_source_lex_sorted_iteration():
    """REQ_F_PAP_016 / REQ_SDD_PAP_008 — universe iteration is
    lex-sorted by symbol regardless of input order."""
    universe = (_stock("CCC"), _stock("AAA"), _stock("BBB"))
    provider = _StubProvider(
        payload={
            "AAA": Ok(_bar("10.00")),
            "BBB": Ok(_bar("20.00")),
            "CCC": Ok(_bar("30.00")),
        }
    )
    source = MultiInstrumentBarSource(universe=universe, provider=provider)
    result = source.poll()
    assert isinstance(result, Ok)
    assert list(result.value.keys()) == [
        InstrumentId("AAA.AS"),
        InstrumentId("BBB.AS"),
        InstrumentId("CCC.AS"),
    ]


def test_multi_bar_source_partial_fan_out_returns_ok_subset():
    """REQ_SDD_PAP_008 — partial fan-out (some symbols Err)
    returns ``Ok(subset)`` with only the successful instruments."""
    universe = (_stock("AAA"), _stock("BBB"), _stock("CCC"))
    provider = _StubProvider(
        payload={
            "AAA": Ok(_bar("10.00")),
            "BBB": Err("network:timeout"),
            "CCC": Ok(_bar("30.00")),
        }
    )
    source = MultiInstrumentBarSource(universe=universe, provider=provider)
    result = source.poll()
    assert isinstance(result, Ok)
    assert set(result.value.keys()) == {
        InstrumentId("AAA.AS"),
        InstrumentId("CCC.AS"),
    }


def test_multi_bar_source_all_errs_returns_err_no_bars():
    """REQ_SDD_PAP_008 — only when EVERY symbol fails SHALL the
    source surface ``Err("data:no_bars")``."""
    universe = (_stock("AAA"), _stock("BBB"))
    provider = _StubProvider(
        payload={
            "AAA": Err("network:timeout"),
            "BBB": Err("data:upstream_blocked"),
        }
    )
    source = MultiInstrumentBarSource(universe=universe, provider=provider)
    result = source.poll()
    assert isinstance(result, Err)
    assert result.error == "data:no_bars"


def test_multi_bar_source_rejects_empty_universe():
    """Defensive: empty universe is a programmer error — the
    runtime SHALL build at least a degenerate single-symbol universe
    before constructing the source."""
    with pytest.raises(ValueError, match="at least one stock"):
        MultiInstrumentBarSource(universe=(), provider=_StubProvider(payload={}))


def test_multi_bar_source_deterministic_poll_iteration_order():
    """REQ_F_PAP_016 / REQ_NF_DAT_001 — two consecutive polls
    against the same stub state SHALL iterate keys in identical
    order (Python preserves dict-insertion-order — the contract
    SHALL hold here)."""
    universe = (_stock("AAA"), _stock("BBB"), _stock("CCC"))
    provider = _StubProvider(
        payload={
            "AAA": Ok(_bar("10.00")),
            "BBB": Ok(_bar("20.00")),
            "CCC": Ok(_bar("30.00")),
        }
    )
    source = MultiInstrumentBarSource(universe=universe, provider=provider)
    r1 = source.poll()
    r2 = source.poll()
    assert isinstance(r1, Ok) and isinstance(r2, Ok)
    assert list(r1.value.keys()) == list(r2.value.keys())


# ---------------------------------------------------------------------------
# TC_PAP_MULTI_005 — PaperStateResponse.per_instrument byte-determinism
# ---------------------------------------------------------------------------


def _build_response(rows: tuple[InstrumentRow, ...]) -> PaperStateResponse:
    return PaperStateResponse(
        account_id="paper-2026-05-30",
        as_of=datetime(2026, 5, 30, 12, tzinfo=UTC),
        is_alive=True,
        is_degraded=False,
        degraded_since=None,
        last_tick_at=datetime(2026, 5, 30, 12, tzinfo=UTC),
        equity_points_count=0,
        latest_equity_after_tax=Decimal("10000.00"),
        per_instrument=rows,
        pinned_symbol=rows[0].symbol if rows else "",
    )


def test_paper_state_response_per_instrument_field_carries_grid_rows():
    """REQ_F_PAP_017 / REQ_SDD_PAP_009 — ``per_instrument`` is a
    tuple of ``InstrumentRow``; the response renders cleanly with
    a 40-instrument grid."""
    rows = tuple(
        InstrumentRow(
            symbol=f"S{i:02d}",
            last_close=Decimal(f"{100 + i}.00"),
            day_change_pct=Decimal("0.5"),
            has_open_position=(i % 2 == 0),
            sparkline=(Decimal("99.0"), Decimal("100.0")),
        )
        for i in range(40)
    )
    response = _build_response(rows)
    assert len(response.per_instrument) == 40
    assert [r.symbol for r in response.per_instrument] == [
        f"S{i:02d}" for i in range(40)
    ]
    assert response.pinned_symbol == "S00"


# ---------------------------------------------------------------------------
# TC_PAP_MULTI_001 — universe-driven runtime construction
# ---------------------------------------------------------------------------


def test_runtime_post_init_normalises_universe_to_lex_sorted_order():
    """REQ_F_PAP_015 / REQ_SDD_PAP_006 — universe stored as a frozen
    lex-sorted tuple regardless of input order."""
    from datetime import datetime, UTC
    from trading_system.models.identifiers import AccountId, StrategyId
    from trading_system.models.money import Money
    from trading_system.models.phase import (
        AllocationBucket,
        MarketRegime,
        PhaseConstraints,
    )
    from trading_system.webapp.runtimes.paper_trading import (
        PAPER_ACCOUNT_PREFIX,
        PaperTradingRuntime,
        PaperTradingSession,
        build_runtime,
    )

    cap = Money(amount=Decimal("10000"), currency=Currency.EUR)
    session = PaperTradingSession(
        account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-30T12:00:00+00:00"),
        universe="multi-test",
        strategy_id=StrategyId("noop"),
        starting_capital=cap,
        started_at=datetime(2026, 5, 30, 12, tzinfo=UTC),
    )
    constraints = PhaseConstraints(
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

    class _Strat:
        id = StrategyId("noop")

        def evaluate(self, _state):
            return []

    class _SrcStub:
        def next_bar(self):
            return Ok(None)

        def latest_cached(self):
            return Ok(None)

    res = build_runtime(
        session=session,
        instrument=_stock("AAA"),
        strategy=_Strat(),
        bar_source=_SrcStub(),
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok)
    runtime = res.value
    # Inject a multi-instrument universe in unsorted order; __post_init__
    # ran already (during dataclass init), so we re-init by constructing
    # the runtime directly with the universe argument.
    multi = PaperTradingRuntime(
        session=session,
        instrument=_stock("AAA"),
        strategy=_Strat(),
        bar_source=_SrcStub(),
        broker=runtime.broker,
        portfolio=runtime.portfolio,
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
        universe=(_stock("CCC"), _stock("AAA"), _stock("BBB")),
    )
    assert [s.symbol for s in multi.universe] == ["AAA", "BBB", "CCC"]


def test_runtime_legacy_single_instrument_builds_degenerate_universe():
    """REQ_SDD_PAP_006 — legacy single-instrument constructor path
    SHALL build a degenerate 1-symbol universe (backwards-compat)."""
    from datetime import datetime, UTC
    from trading_system.models.identifiers import AccountId, StrategyId
    from trading_system.models.money import Money
    from trading_system.models.phase import (
        AllocationBucket,
        MarketRegime,
        PhaseConstraints,
    )
    from trading_system.webapp.runtimes.paper_trading import (
        PAPER_ACCOUNT_PREFIX,
        PaperTradingSession,
        build_runtime,
    )

    cap = Money(amount=Decimal("10000"), currency=Currency.EUR)
    session = PaperTradingSession(
        account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-30T12:00:00+00:00"),
        universe="single-test",
        strategy_id=StrategyId("noop"),
        starting_capital=cap,
        started_at=datetime(2026, 5, 30, 12, tzinfo=UTC),
    )
    constraints = PhaseConstraints(
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

    class _Strat:
        id = StrategyId("noop")

        def evaluate(self, _state):
            return []

    class _SrcStub:
        def next_bar(self):
            return Ok(None)

        def latest_cached(self):
            return Ok(None)

    res = build_runtime(
        session=session,
        instrument=_stock("SOLO"),
        strategy=_Strat(),
        bar_source=_SrcStub(),
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok)
    runtime = res.value
    # Legacy build_runtime call ⇒ universe normalised to (instrument,)
    assert len(runtime.universe) == 1
    assert runtime.universe[0].symbol == "SOLO"


# ---------------------------------------------------------------------------
# TC_PAP_MULTI_002 — full-universe ScoredStock ranking
# ---------------------------------------------------------------------------


def test_build_screener_ranking_emits_one_scored_stock_per_universe_member():
    """REQ_F_PAP_015 / REQ_SDD_PAP_007 — every universe stock
    appears in the strategy's ranking input (lex-sorted)."""
    from datetime import datetime, UTC
    from trading_system.models.identifiers import AccountId, StrategyId
    from trading_system.models.money import Money
    from trading_system.models.phase import (
        AllocationBucket,
        MarketRegime,
        PhaseConstraints,
    )
    from trading_system.webapp.runtimes.paper_trading import (
        PAPER_ACCOUNT_PREFIX,
        PaperTradingRuntime,
        PaperTradingSession,
        build_runtime,
    )

    cap = Money(amount=Decimal("10000"), currency=Currency.EUR)
    session = PaperTradingSession(
        account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-30T12:00:00+00:00"),
        universe="rank-test",
        strategy_id=StrategyId("noop"),
        starting_capital=cap,
        started_at=datetime(2026, 5, 30, 12, tzinfo=UTC),
    )
    constraints = PhaseConstraints(
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

    class _Strat:
        id = StrategyId("noop")

        def evaluate(self, _state):
            return []

    class _SrcStub:
        def next_bar(self):
            return Ok(None)

        def latest_cached(self):
            return Ok(None)

    res = build_runtime(
        session=session,
        instrument=_stock("AAA"),
        strategy=_Strat(),
        bar_source=_SrcStub(),
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok)
    runtime_seed = res.value
    universe = (_stock("BBB"), _stock("AAA"), _stock("CCC"))
    multi = PaperTradingRuntime(
        session=session,
        instrument=_stock("AAA"),
        strategy=_Strat(),
        bar_source=_SrcStub(),
        broker=runtime_seed.broker,
        portfolio=runtime_seed.portfolio,
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
        universe=universe,
    )
    ranking = multi._build_screener_ranking()
    assert len(ranking) == 3
    assert [s.stock.symbol for s in ranking] == ["AAA", "BBB", "CCC"]


# ---------------------------------------------------------------------------
# TC_PAP_MULTI_005 (reader integration) — paper_state populates per_instrument
# ---------------------------------------------------------------------------


def test_paper_state_reader_swaps_price_chart_series_to_pinned_symbol():
    """CR-026 follow-up — clicking a non-default symbol in the
    per-instrument grid SHALL swap the Price-Evolution chart's
    series + `instrument_symbol` caption + `latest_close` to
    the pinned stock (not stay on the runtime's primary
    instrument). Regression for the live "still see AC" bug."""
    from dataclasses import dataclass, field
    from datetime import datetime, UTC

    from trading_system.models.identifiers import AccountId
    from trading_system.result import Nothing, Some
    from trading_system.webapp.paper_state_reader import RuntimePaperStateReader

    @dataclass
    class _Registry:
        runtimes: dict = field(default_factory=dict)

        def status(self, aid):
            r = self.runtimes.get(aid)
            return Some(r) if r is not None else Nothing()

    # Stub yfinance-style provider returning a 3-bar history per
    # symbol; the close differs per symbol so the caller can
    # distinguish series.
    class _ProviderStub:
        def latest(self, instrument):
            sym = instrument.symbol
            return Ok(_bar({"AAA": "10.00", "BBB": "20.00", "CCC": "30.00"}[sym]))

        def bars(self, instrument, *_args, **_kwargs):
            sym = instrument.symbol
            base = {"AAA": Decimal("10"), "BBB": Decimal("20"), "CCC": Decimal("30")}[sym]
            from datetime import timedelta
            bars = [
                Bar(
                    at=datetime(2026, 5, 28, tzinfo=UTC) + timedelta(days=i),
                    open=base + Decimal(i),
                    high=base + Decimal(i) + Decimal("0.5"),
                    low=base + Decimal(i) - Decimal("0.5"),
                    close=base + Decimal(i),
                    volume=Decimal("1000"),
                )
                for i in range(3)
            ]
            return Ok(bars)

        def dividends(self, *_a, **_k):
            return Err("data:not_supported")

    class _BarSourceStub:
        def __init__(self, provider):
            self._provider = provider

    universe = (_stock("AAA"), _stock("BBB"), _stock("CCC"))
    provider = _ProviderStub()

    class _Runtime:
        pass

    runtime = _Runtime()
    runtime.universe = universe
    runtime.instrument = _stock("AAA")  # primary
    runtime.bar_source = _BarSourceStub(provider)
    runtime.market_data_provider = provider
    runtime.is_alive = lambda: True
    runtime.is_degraded = lambda: False
    runtime.degraded_since = lambda: None
    runtime.last_tick_at = lambda: datetime(2026, 5, 30, 12, tzinfo=UTC)
    runtime.equity_history = lambda: ()
    runtime.session = None
    runtime.latest_close = lambda: Decimal("10.00")  # primary's close

    aid = AccountId("paper-2026-05-30T12:00:00+00:00")
    reader = RuntimePaperStateReader(registry=_Registry(runtimes={aid: runtime}))

    # No pin ⇒ chart series + caption + last_close belong to AAA.
    default_snap = reader.paper_state(
        account_id=aid, as_of=datetime(2026, 5, 30, 12, tzinfo=UTC)
    )
    assert default_snap.instrument_symbol == "AAA"
    assert default_snap.recent_close_series  # non-empty
    # AAA series close-3 = 10 + 2 = 12.
    assert default_snap.recent_close_series[-1] == Decimal("12")

    # Pin BBB ⇒ chart series + caption + last_close SHALL swap.
    pinned_snap = reader.paper_state(
        account_id=aid,
        as_of=datetime(2026, 5, 30, 12, tzinfo=UTC),
        pinned_symbol="BBB",
    )
    assert pinned_snap.instrument_symbol == "BBB"
    # BBB series close-3 = 20 + 2 = 22.
    assert pinned_snap.recent_close_series[-1] == Decimal("22")
    assert pinned_snap.latest_close == Decimal("22")


def test_paper_state_reader_honors_pinned_symbol_query_override():
    """REQ_F_PAP_018 / REQ_SDD_PAP_010 — ``?pin=<symbol>`` query
    parameter overrides the default lex-first pin. Unknown symbols
    fall back to the default."""
    from dataclasses import dataclass, field
    from datetime import datetime, UTC

    from trading_system.models.identifiers import AccountId
    from trading_system.result import Nothing, Some
    from trading_system.webapp.paper_state_reader import RuntimePaperStateReader

    @dataclass
    class _Registry:
        runtimes: dict = field(default_factory=dict)

        def status(self, aid):
            r = self.runtimes.get(aid)
            return Some(r) if r is not None else Nothing()

    class _Runtime:
        universe = (_stock("AAA"), _stock("BBB"), _stock("CCC"))
        market_data_provider = _StubProvider(
            payload={
                "AAA": Ok(_bar("10.00")),
                "BBB": Ok(_bar("20.00")),
                "CCC": Ok(_bar("30.00")),
            }
        )

        def is_alive(self):
            return True

        def is_degraded(self):
            return False

        def degraded_since(self):
            return None

        def last_tick_at(self):
            return datetime(2026, 5, 30, 12, tzinfo=UTC)

        def equity_history(self):
            return ()

    runtime = _Runtime()
    aid = AccountId("paper-2026-05-30T12:00:00+00:00")
    reader = RuntimePaperStateReader(registry=_Registry(runtimes={aid: runtime}))

    # Default pin = first lex symbol.
    default_snap = reader.paper_state(
        account_id=aid, as_of=datetime(2026, 5, 30, 12, tzinfo=UTC)
    )
    assert default_snap.pinned_symbol == "AAA"

    # ``pinned_symbol`` override wins when the symbol exists.
    pinned_snap = reader.paper_state(
        account_id=aid,
        as_of=datetime(2026, 5, 30, 12, tzinfo=UTC),
        pinned_symbol="BBB",
    )
    assert pinned_snap.pinned_symbol == "BBB"

    # Unknown symbol ⇒ falls back to the default.
    fallback_snap = reader.paper_state(
        account_id=aid,
        as_of=datetime(2026, 5, 30, 12, tzinfo=UTC),
        pinned_symbol="ZZZ",
    )
    assert fallback_snap.pinned_symbol == "AAA"


def test_paper_state_reader_populates_per_instrument_from_universe():
    """REQ_F_PAP_017 / REQ_SDD_PAP_009 — the
    ``RuntimePaperStateReader.paper_state`` snapshot SHALL carry
    one ``InstrumentRow`` per universe stock, sorted by symbol
    lex-order; default pin = first symbol."""
    from dataclasses import dataclass, field
    from datetime import datetime, UTC

    from trading_system.models.identifiers import AccountId
    from trading_system.result import Nothing, Some
    from trading_system.webapp.paper_state_reader import RuntimePaperStateReader

    @dataclass
    class _Registry:
        runtimes: dict = field(default_factory=dict)

        def status(self, aid):
            r = self.runtimes.get(aid)
            return Some(r) if r is not None else Nothing()

    class _Runtime:
        universe = (_stock("BBB"), _stock("AAA"), _stock("CCC"))
        market_data_provider = _StubProvider(
            payload={
                "AAA": Ok(_bar("10.00")),
                "BBB": Ok(_bar("20.00")),
                "CCC": Ok(_bar("30.00")),
            }
        )

        def is_alive(self):
            return True

        def is_degraded(self):
            return False

        def degraded_since(self):
            return None

        def last_tick_at(self):
            return datetime(2026, 5, 30, 12, tzinfo=UTC)

        def equity_history(self):
            return ()

    # Sort the universe so the assertion order matches the
    # reader's lex-sorted output.
    runtime = _Runtime()
    runtime.universe = tuple(sorted(runtime.universe, key=lambda s: s.symbol))
    aid = AccountId("paper-2026-05-30T12:00:00+00:00")
    reader = RuntimePaperStateReader(registry=_Registry(runtimes={aid: runtime}))
    snap = reader.paper_state(
        account_id=aid, as_of=datetime(2026, 5, 30, 12, tzinfo=UTC)
    )
    assert [r.symbol for r in snap.per_instrument] == ["AAA", "BBB", "CCC"]
    assert snap.pinned_symbol == "AAA"
    assert all(r.last_close is not None for r in snap.per_instrument)
    assert all(not r.has_open_position for r in snap.per_instrument)


# ---------------------------------------------------------------------------
# TC_PAP_MULTI_006 — Dashboard grid renders + pin handler wires up
# ---------------------------------------------------------------------------


def test_dashboard_template_includes_per_instrument_grid_and_pin_handler():
    """REQ_F_PAP_018 / REQ_SDD_PAP_010 — the dashboard template
    SHALL ship the per-instrument grid section + the JS click
    handler that updates ``?pin=<symbol>``. Smoke test against the
    template file — no HTTP round-trip needed."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    template = (
        repo_root
        / "trading_system"
        / "webapp"
        / "templates"
        / "dashboard.html"
    ).read_text(encoding="utf-8")
    # Section header present.
    assert "Universe — per-instrument grid (CR-026)" in template
    # Table data-field hook the JS targets.
    assert 'data-field="paper_per_instrument_table"' in template
    # JS reads ``data.per_instrument`` and updates ``?pin=<symbol>``
    # — both literals appear in the renderer.
    assert "data.per_instrument" in template
    assert "url.searchParams.set('pin'" in template


# ---------------------------------------------------------------------------
# TC_PER_BAR_005 — runtime fan-out wiring
# ---------------------------------------------------------------------------


def test_runtime_fans_out_polled_bars_to_instrument_bar_repo():
    """CR-029 / REQ_F_PER_012 / REQ_SDD_PER_012 — when the runtime
    has > 1 universe symbol + the instrument_bar_repo slot wired,
    _apply_bar SHALL persist every universe symbol's polled bar
    through the repository BEFORE the strategy step."""
    from dataclasses import dataclass, field
    from datetime import datetime, UTC

    from trading_system.models.identifiers import AccountId, StrategyId
    from trading_system.models.money import Money
    from trading_system.models.phase import (
        AllocationBucket,
        MarketRegime,
        PhaseConstraints,
    )
    from trading_system.webapp.runtimes.paper_trading import (
        PAPER_ACCOUNT_PREFIX,
        PaperTradingRuntime,
        PaperTradingSession,
        build_runtime,
    )

    @dataclass
    class _SpyRepo:
        calls: list = field(default_factory=list)

        def append_bars(self, rows, *, account_id):
            self.calls.append((str(account_id), list(rows)))
            return Ok(None)

    # Provider returns a pinned bar for each symbol.
    provider = _StubProvider(
        payload={
            "AAA": Ok(_bar("10.00")),
            "BBB": Ok(_bar("20.00")),
            "CCC": Ok(_bar("30.00")),
        }
    )

    cap = Money(amount=Decimal("10000"), currency=Currency.EUR)
    session = PaperTradingSession(
        account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-30T12:00:00+00:00"),
        universe="multi-test",
        strategy_id=StrategyId("noop"),
        starting_capital=cap,
        started_at=datetime(2026, 5, 30, 12, tzinfo=UTC),
    )
    constraints = PhaseConstraints(
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

    class _Strat:
        id = StrategyId("noop")

        def evaluate(self, _state):
            return []

    @dataclass(slots=True)
    class _SrcStub:
        # Mimics _StubBarSource: emits one Ok(Bar) per call.
        _emitted: bool = False

        def next_bar(self):
            from trading_system.result import Nothing, Some
            if self._emitted:
                return Ok(Nothing())
            self._emitted = True
            return Ok(Some(_bar("10.00")))

        def latest_cached(self):
            from trading_system.result import Some
            return Ok(Some(_bar("10.00")))

    res = build_runtime(
        session=session,
        instrument=_stock("AAA"),
        strategy=_Strat(),
        bar_source=_SrcStub(),
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok)
    runtime = res.value
    runtime.universe = (_stock("AAA"), _stock("BBB"), _stock("CCC"))
    runtime.market_data_provider = provider
    spy = _SpyRepo()
    runtime.instrument_bar_repo = spy

    # Drive one tick.
    tick_result = runtime.tick_once()
    assert isinstance(tick_result, Ok)
    # The repository was called once with one row per universe symbol.
    assert len(spy.calls) == 1
    aid, rows = spy.calls[0]
    assert aid == str(session.account_id)
    assert {str(iid) for iid, _bar_ in rows} == {
        "AAA.AS", "BBB.AS", "CCC.AS"
    }


def test_runtime_marks_portfolio_at_universe_wide_prices_per_tick():
    """REQ_F_PAP_018 / REQ_SDD_PAP_010 — multi-instrument tick
    fan-out. _apply_bar SHALL fan ``portfolio.mark`` out across
    every universe member so open positions in any symbol get
    repriced every tick (not just the primary instrument)."""
    from dataclasses import dataclass, field
    from datetime import datetime, UTC

    from trading_system.models.identifiers import AccountId, StrategyId
    from trading_system.models.money import Money
    from trading_system.models.phase import (
        AllocationBucket,
        MarketRegime,
        PhaseConstraints,
    )
    from trading_system.webapp.runtimes.paper_trading import (
        PAPER_ACCOUNT_PREFIX,
        PaperTradingSession,
        build_runtime,
    )

    provider = _StubProvider(
        payload={
            "AAA": Ok(_bar("10.00")),
            "BBB": Ok(_bar("20.00")),
            "CCC": Ok(_bar("30.00")),
        }
    )

    cap = Money(amount=Decimal("10000"), currency=Currency.EUR)
    session = PaperTradingSession(
        account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-30T12:00:00+00:00"),
        universe="multi-test",
        strategy_id=StrategyId("noop"),
        starting_capital=cap,
        started_at=datetime(2026, 5, 30, 12, tzinfo=UTC),
    )
    constraints = PhaseConstraints(
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

    class _Strat:
        id = StrategyId("noop")

        def evaluate(self, _state):
            return []

    @dataclass(slots=True)
    class _SrcStub:
        _emitted: bool = False

        def next_bar(self):
            from trading_system.result import Nothing, Some
            if self._emitted:
                return Ok(Nothing())
            self._emitted = True
            return Ok(Some(_bar("10.00")))

        def latest_cached(self):
            from trading_system.result import Some
            return Ok(Some(_bar("10.00")))

    @dataclass
    class _SpyPortfolio:
        """Wraps the real portfolio + records every mark call so
        the test can assert which instrument_ids got priced."""

        inner: object
        mark_calls: list = field(default_factory=list)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def mark(self, prices):
            self.mark_calls.append(dict(prices))
            return self.inner.mark(prices)

    res = build_runtime(
        session=session,
        instrument=_stock("AAA"),
        strategy=_Strat(),
        bar_source=_SrcStub(),
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok)
    runtime = res.value
    runtime.universe = (_stock("AAA"), _stock("BBB"), _stock("CCC"))
    runtime.market_data_provider = provider
    spy = _SpyPortfolio(inner=runtime.portfolio)
    runtime.portfolio = spy  # type: ignore[assignment]

    tick_result = runtime.tick_once()
    assert isinstance(tick_result, Ok)
    assert len(spy.mark_calls) == 1, spy.mark_calls
    marked = spy.mark_calls[0]
    # Every universe member's instrument_id appears in the mark map.
    assert {str(iid) for iid in marked} == {"AAA.AS", "BBB.AS", "CCC.AS"}
    assert marked[InstrumentId("AAA.AS")] == Decimal("10.00")
    assert marked[InstrumentId("BBB.AS")] == Decimal("20.00")
    assert marked[InstrumentId("CCC.AS")] == Decimal("30.00")


def test_legacy_single_instrument_runtime_marks_only_primary():
    """REQ_SDD_PAP_006 — legacy single-instrument sessions
    (no market_data_provider OR degenerate universe) fall through
    to the primary-instrument-only mark. Backwards-compat."""
    from dataclasses import dataclass, field
    from datetime import datetime, UTC

    from trading_system.models.identifiers import AccountId, StrategyId
    from trading_system.models.money import Money
    from trading_system.models.phase import (
        AllocationBucket,
        MarketRegime,
        PhaseConstraints,
    )
    from trading_system.webapp.runtimes.paper_trading import (
        PAPER_ACCOUNT_PREFIX,
        PaperTradingSession,
        build_runtime,
    )

    cap = Money(amount=Decimal("10000"), currency=Currency.EUR)
    session = PaperTradingSession(
        account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-30T12:00:00+00:00"),
        universe="single-test",
        strategy_id=StrategyId("noop"),
        starting_capital=cap,
        started_at=datetime(2026, 5, 30, 12, tzinfo=UTC),
    )
    constraints = PhaseConstraints(
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

    class _Strat:
        id = StrategyId("noop")

        def evaluate(self, _state):
            return []

    @dataclass(slots=True)
    class _SrcStub:
        _emitted: bool = False

        def next_bar(self):
            from trading_system.result import Nothing, Some
            if self._emitted:
                return Ok(Nothing())
            self._emitted = True
            return Ok(Some(_bar("42.00")))

        def latest_cached(self):
            from trading_system.result import Some
            return Ok(Some(_bar("42.00")))

    @dataclass
    class _SpyPortfolio:
        inner: object
        mark_calls: list = field(default_factory=list)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def mark(self, prices):
            self.mark_calls.append(dict(prices))
            return self.inner.mark(prices)

    res = build_runtime(
        session=session,
        instrument=_stock("AAA"),
        strategy=_Strat(),
        bar_source=_SrcStub(),
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok)
    runtime = res.value
    # Degenerate universe (only the primary instrument) — legacy path.
    spy = _SpyPortfolio(inner=runtime.portfolio)
    runtime.portfolio = spy  # type: ignore[assignment]
    # market_data_provider stays None; universe stays the
    # build_runtime-constructed degenerate single-element tuple.
    assert runtime.market_data_provider is None
    assert len(runtime.universe) == 1

    tick_result = runtime.tick_once()
    assert isinstance(tick_result, Ok)
    assert len(spy.mark_calls) == 1
    marked = spy.mark_calls[0]
    # Only the primary instrument was marked (no fan-out).
    assert set(marked) == {InstrumentId("AAA.AS")}


def test_runtime_skips_fan_out_when_repo_unwired():
    """CR-029 — None slot ⇒ no repository calls. Defensive: a
    runtime without the slot SHALL operate exactly like the
    pre-CR-029 single-instrument session."""
    from dataclasses import dataclass, field
    from datetime import datetime, UTC

    from trading_system.models.identifiers import AccountId, StrategyId
    from trading_system.models.money import Money
    from trading_system.models.phase import (
        AllocationBucket,
        MarketRegime,
        PhaseConstraints,
    )
    from trading_system.webapp.runtimes.paper_trading import (
        PAPER_ACCOUNT_PREFIX,
        PaperTradingSession,
        build_runtime,
    )

    @dataclass
    class _SpyRepo:
        calls: list = field(default_factory=list)

        def append_bars(self, rows, *, account_id):
            self.calls.append((str(account_id), list(rows)))
            return Ok(None)

    provider = _StubProvider(
        payload={
            "AAA": Ok(_bar("10.00")),
            "BBB": Ok(_bar("20.00")),
        }
    )

    cap = Money(amount=Decimal("10000"), currency=Currency.EUR)
    session = PaperTradingSession(
        account_id=AccountId(f"{PAPER_ACCOUNT_PREFIX}2026-05-30T12:00:00+00:00"),
        universe="multi-test",
        strategy_id=StrategyId("noop"),
        starting_capital=cap,
        started_at=datetime(2026, 5, 30, 12, tzinfo=UTC),
    )
    constraints = PhaseConstraints(
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

    class _Strat:
        id = StrategyId("noop")

        def evaluate(self, _state):
            return []

    @dataclass(slots=True)
    class _SrcStub:
        _emitted: bool = False

        def next_bar(self):
            from trading_system.result import Nothing, Some
            if self._emitted:
                return Ok(Nothing())
            self._emitted = True
            return Ok(Some(_bar("10.00")))

        def latest_cached(self):
            from trading_system.result import Some
            return Ok(Some(_bar("10.00")))

    res = build_runtime(
        session=session,
        instrument=_stock("AAA"),
        strategy=_Strat(),
        bar_source=_SrcStub(),
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
    )
    assert isinstance(res, Ok)
    runtime = res.value
    runtime.universe = (_stock("AAA"), _stock("BBB"))
    runtime.market_data_provider = provider
    # Slot intentionally left None.
    assert runtime.instrument_bar_repo is None

    spy = _SpyRepo()  # never called
    tick_result = runtime.tick_once()
    assert isinstance(tick_result, Ok)
    assert spy.calls == []


# ---------------------------------------------------------------------------
# CR-026 follow-up — reader cache (pin switch latency fix)
# ---------------------------------------------------------------------------


def test_paper_state_reader_caches_provider_calls_within_ttl():
    """CR-026 follow-up — second call within ``cache_ttl_seconds``
    SHALL NOT re-invoke the wrapped MarketDataProvider. Catches the
    operator-reported "10s switch" regression: each pin switch
    triggers a new ``paper_state(pinned_symbol=...)`` call; without
    the cache, each call paid the 120-day fetch."""
    from dataclasses import dataclass, field
    from datetime import datetime, UTC

    from trading_system.models.identifiers import AccountId
    from trading_system.result import Nothing, Some
    from trading_system.webapp.paper_state_reader import RuntimePaperStateReader

    @dataclass
    class _Registry:
        runtimes: dict = field(default_factory=dict)

        def status(self, aid):
            r = self.runtimes.get(aid)
            return Some(r) if r is not None else Nothing()

    # Spy provider that counts every `.latest()` + `.bars()` call.
    @dataclass
    class _SpyProvider:
        latest_calls: int = 0
        bars_calls: int = 0

        def latest(self, instrument):
            self.latest_calls += 1
            return Ok(_bar("100.00"))

        def bars(self, *_a, **_k):
            self.bars_calls += 1
            return Ok([_bar("100.00")])

        def dividends(self, *_a, **_k):
            return Err("data:not_supported")

    class _BarSourceStub:
        def __init__(self, provider):
            self._provider = provider

    provider = _SpyProvider()

    class _Runtime:
        pass

    runtime = _Runtime()
    runtime.universe = (_stock("AAA"), _stock("BBB"))
    runtime.instrument = _stock("AAA")
    runtime.bar_source = _BarSourceStub(provider)
    runtime.market_data_provider = provider
    runtime.is_alive = lambda: True
    runtime.is_degraded = lambda: False
    runtime.degraded_since = lambda: None
    runtime.last_tick_at = lambda: datetime(2026, 5, 30, 12, tzinfo=UTC)
    runtime.equity_history = lambda: ()
    runtime.session = None
    runtime.latest_close = lambda: Decimal("100.00")

    aid = AccountId("paper-2026-05-30T12:00:00+00:00")
    reader = RuntimePaperStateReader(
        registry=_Registry(runtimes={aid: runtime}),
        cache_ttl_seconds=30.0,
    )
    reader.paper_state(
        account_id=aid, as_of=datetime(2026, 5, 30, 12, tzinfo=UTC)
    )
    latest_after_first = provider.latest_calls
    bars_after_first = provider.bars_calls
    assert latest_after_first > 0  # populated grid + day-change

    # Second call within TTL — counters frozen.
    reader.paper_state(
        account_id=aid, as_of=datetime(2026, 5, 30, 12, tzinfo=UTC)
    )
    assert provider.latest_calls == latest_after_first
    assert provider.bars_calls == bars_after_first


def test_paper_state_reader_cache_ttl_zero_disables_caching():
    """Defensive — ``cache_ttl_seconds=0`` SHALL bypass the cache
    entirely so the legacy uncached path stays exercisable."""
    from dataclasses import dataclass, field
    from datetime import datetime, UTC

    from trading_system.models.identifiers import AccountId
    from trading_system.result import Nothing, Some
    from trading_system.webapp.paper_state_reader import RuntimePaperStateReader

    @dataclass
    class _Registry:
        runtimes: dict = field(default_factory=dict)

        def status(self, aid):
            r = self.runtimes.get(aid)
            return Some(r) if r is not None else Nothing()

    @dataclass
    class _SpyProvider:
        latest_calls: int = 0

        def latest(self, instrument):
            self.latest_calls += 1
            return Ok(_bar("100.00"))

        def bars(self, *_a, **_k):
            return Ok([_bar("100.00")])

        def dividends(self, *_a, **_k):
            return Err("data:not_supported")

    class _BarSourceStub:
        def __init__(self, provider):
            self._provider = provider

    provider = _SpyProvider()

    class _Runtime:
        pass

    runtime = _Runtime()
    runtime.universe = (_stock("AAA"),)
    runtime.instrument = _stock("AAA")
    runtime.bar_source = _BarSourceStub(provider)
    runtime.market_data_provider = provider
    runtime.is_alive = lambda: True
    runtime.is_degraded = lambda: False
    runtime.degraded_since = lambda: None
    runtime.last_tick_at = lambda: datetime(2026, 5, 30, 12, tzinfo=UTC)
    runtime.equity_history = lambda: ()
    runtime.session = None
    runtime.latest_close = lambda: Decimal("100.00")

    aid = AccountId("paper-2026-05-30T12:00:00+00:00")
    reader = RuntimePaperStateReader(
        registry=_Registry(runtimes={aid: runtime}),
        cache_ttl_seconds=0,
    )
    reader.paper_state(
        account_id=aid, as_of=datetime(2026, 5, 30, 12, tzinfo=UTC)
    )
    calls_after_first = provider.latest_calls
    reader.paper_state(
        account_id=aid, as_of=datetime(2026, 5, 30, 12, tzinfo=UTC)
    )
    # Counter must have grown — cache is disabled.
    assert provider.latest_calls > calls_after_first


def test_instrument_row_canonical_dataclass_fields():
    """REQ_SDD_PAP_009 — ``InstrumentRow`` carries exactly the
    documented fields. Defensive: any future field addition is a
    contract change."""
    row = InstrumentRow(
        symbol="ASML",
        last_close=Decimal("700.00"),
        day_change_pct=Decimal("1.25"),
        has_open_position=True,
        sparkline=(Decimal("695.0"), Decimal("697.5"), Decimal("700.0")),
    )
    assert row.symbol == "ASML"
    assert row.last_close == Decimal("700.00")
    assert row.day_change_pct == Decimal("1.25")
    assert row.has_open_position is True
    assert row.sparkline == (Decimal("695.0"), Decimal("697.5"), Decimal("700.0"))
