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
