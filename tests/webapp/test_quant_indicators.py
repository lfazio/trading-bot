"""Tests for the dashboard quant-indicators panel (REQ_F_WEB2_010)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import AccountId
from trading_system.models.money import Currency, Money
from trading_system.result import Nothing, Some
from trading_system.webapp.paper_state_reader import RuntimePaperStateReader
from trading_system.webapp.runtimes.quant_indicators import (
    compute_indicators,
)


# ---------------------------------------------------------------------------
# Pure-function tests on compute_indicators
# ---------------------------------------------------------------------------


def _d(x: str) -> Decimal:
    return Decimal(x)


def test_compute_indicators_empty_returns_all_none() -> None:
    snap = compute_indicators([], [])
    assert snap.sma_20 is None
    assert snap.sma_50 is None
    assert snap.realized_vol_pct is None
    assert snap.total_return_pct is None
    assert snap.drawdown_pct is None
    assert snap.sharpe_ratio is None
    assert snap.trend_signal == "n/a"
    assert snap.regime == "n/a"


def test_sma_20_returns_none_when_under_20_bars() -> None:
    closes = [_d("100") for _ in range(10)]
    snap = compute_indicators(closes, [])
    assert snap.sma_20 is None
    assert snap.sma_50 is None
    assert snap.trend_signal == "n/a"


def test_sma_20_matches_arithmetic_mean_of_last_20() -> None:
    closes = [_d(str(i)) for i in range(1, 51)]  # 1..50
    snap = compute_indicators(closes, [])
    # last 20 are 31..50 ⇒ mean = (31+50)/2 = 40.5
    assert snap.sma_20 == _d("40.5000")
    # last 50 are 1..50 ⇒ mean = 25.5
    assert snap.sma_50 == _d("25.5000")
    # 40.5 > 25.5 ⇒ uptrend
    assert snap.trend_signal == "up"


def test_trend_down_when_sma_short_below_long() -> None:
    closes = [_d(str(i)) for i in range(50, 0, -1)]  # 50..1
    snap = compute_indicators(closes, [])
    # last 20 are 20..1 ⇒ mean = 10.5; last 50 are 50..1 ⇒ mean = 25.5
    assert snap.sma_20 is not None and snap.sma_50 is not None
    assert snap.sma_20 < snap.sma_50
    assert snap.trend_signal == "down"


def test_total_return_pct_handles_zero_first_value() -> None:
    snap = compute_indicators([_d("0"), _d("100")], [])
    assert snap.total_return_pct is None


def test_total_return_pct_basic_math() -> None:
    snap = compute_indicators([_d("100"), _d("120")], [])
    assert snap.total_return_pct == _d("20.00")


def test_drawdown_zero_for_monotonic_up_series() -> None:
    snap = compute_indicators(
        [_d("100")],
        [_d("100"), _d("110"), _d("120"), _d("130")],
    )
    assert snap.drawdown_pct == _d("0.00")


def test_drawdown_reports_largest_peak_to_trough() -> None:
    snap = compute_indicators(
        [_d("100")],
        [_d("100"), _d("120"), _d("90"), _d("110")],
    )
    # peak=120, trough=90 ⇒ dd = 25.00%
    assert snap.drawdown_pct == _d("25.00")


def test_sharpe_returns_none_for_too_few_samples() -> None:
    snap = compute_indicators([_d("100")], [_d("100"), _d("110")])
    assert snap.sharpe_ratio is None


def test_realized_vol_returns_none_for_too_few_bars() -> None:
    closes = [_d("100") for _ in range(10)]
    snap = compute_indicators(closes, [])
    assert snap.realized_vol_pct is None


def test_realized_vol_is_zero_for_constant_series() -> None:
    closes = [_d("100") for _ in range(30)]
    snap = compute_indicators(closes, [])
    # 21+ samples ⇒ a value is produced; constant series ⇒ ~0%.
    assert snap.realized_vol_pct == _d("0.00")


def test_regime_pass_through() -> None:
    snap = compute_indicators([_d("100")], [], regime="BULL")
    assert snap.regime == "BULL"


# ---------------------------------------------------------------------------
# Reader integration — quant fields populate PaperStateResponse
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakeBarSource:
    closes: list[Decimal]

    def history(self):
        from trading_system.data.types import Bar

        base = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
        bars: list = []
        for i, c in enumerate(self.closes):
            bars.append(
                Bar(
                    at=base + timedelta(minutes=i),
                    open=c,
                    high=c * Decimal("1.001"),
                    low=c * Decimal("0.999"),
                    close=c,
                    volume=Decimal("1000"),
                )
            )
        return tuple(bars)


@dataclass(slots=True)
class _FakeRuntime:
    closes: list[Decimal] = field(default_factory=list)
    points: list[EquityPoint] = field(default_factory=list)
    alive: bool = True

    @property
    def bar_source(self):
        return _FakeBarSource(closes=self.closes)

    @property
    def regime(self):
        from trading_system.models.phase import MarketRegime

        return MarketRegime.BULL

    def is_alive(self) -> bool:
        return self.alive

    def is_degraded(self) -> bool:
        return False

    def degraded_since(self):
        return None

    def last_tick_at(self):
        return None

    def equity_history(self):
        return tuple(self.points)


@dataclass(slots=True)
class _FakeRegistry:
    runtimes: dict[AccountId, _FakeRuntime] = field(default_factory=dict)

    def status(self, account_id: AccountId):
        runtime = self.runtimes.get(account_id)
        if runtime is None:
            return Nothing()
        return Some(runtime)


_AID = AccountId("paper-quant-test")
_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _point(*, at: datetime, amount: str) -> EquityPoint:
    return EquityPoint(
        at=at,
        equity_gross=Money(Decimal(amount), Currency.EUR),
        equity_after_tax=Money(Decimal(amount), Currency.EUR),
        drawdown_pct=Decimal("0"),
    )


def test_reader_populates_quant_fields_when_history_available() -> None:
    closes = [_d(str(i)) for i in range(1, 51)]
    points = [
        _point(at=_NOW + timedelta(minutes=i), amount=str(1000 + i))
        for i in range(10)
    ]
    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_AID: _FakeRuntime(closes=closes, points=points)})
    )
    snap = reader.paper_state(account_id=_AID, as_of=_NOW)
    assert snap.regime == "BULL"
    assert snap.trend_signal == "up"
    assert snap.sma_20 == _d("40.5000")
    assert snap.sma_50 == _d("25.5000")
    assert snap.total_return_pct == _d("4900.00")  # 1 -> 50
    assert snap.drawdown_pct == _d("0.00")
    assert snap.sharpe_ratio is not None


def test_reader_emits_recent_close_series_for_dashboard_chart() -> None:
    """REQ_F_WEB2_010 follow-up — the SSE payload SHALL carry the
    recent close series + timestamps so the dashboard can render
    an inline sparkline. Capped at 60 bars."""
    # 80 bars — the reader SHALL trim to the last 60.
    closes = [_d(str(i)) for i in range(1, 81)]
    runtime = _FakeRuntime(closes=closes, points=[])
    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_AID: runtime})
    )
    snap = reader.paper_state(account_id=_AID, as_of=_NOW)
    assert len(snap.recent_close_series) == 60
    assert len(snap.recent_close_timestamps) == 60
    # The trimmed window keeps the LAST 60 bars (so closes 21..80).
    assert snap.recent_close_series[0] == _d("21")
    assert snap.recent_close_series[-1] == _d("80")


def test_reader_emits_sma_overlays_aligned_with_close_window() -> None:
    """SMA-20 / SMA-50 series SHALL be parallel to the trimmed
    close window: same length, None at indices that don't have
    enough history yet."""
    closes = [_d(str(i)) for i in range(1, 81)]
    runtime = _FakeRuntime(closes=closes, points=[])
    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_AID: runtime})
    )
    snap = reader.paper_state(account_id=_AID, as_of=_NOW)
    assert len(snap.recent_sma20_series) == 60
    assert len(snap.recent_sma50_series) == 60
    # SMA20 needs 20 bars; with 80 closes the rolling output is
    # defined at indices 19..79 (61 values). When trimmed to the
    # last 60, every entry SHALL be defined.
    assert all(v is not None for v in snap.recent_sma20_series)
    # SMA50 needs 50 bars; defined at indices 49..79 (31 values).
    # The trim-to-60 keeps indices 20..79 — so indices 20..48 (29
    # positions) hold None and 49..79 (31 positions) hold values.
    defined_sma50 = [v for v in snap.recent_sma50_series if v is not None]
    assert len(defined_sma50) == 31
    # SMA20 at the last position = mean of closes 61..80 = 70.5
    assert snap.recent_sma20_series[-1] == _d("70.5")
    # SMA50 at the last position = mean of closes 31..80 = 55.5
    assert snap.recent_sma50_series[-1] == _d("55.5")


def test_reader_emits_partial_sma_when_window_underfull() -> None:
    """If the close series is shorter than the SMA window, the
    early indices SHALL hold None rather than producing garbage."""
    # 10 closes — not enough for SMA20 anywhere; SMA50 never
    # defined.
    closes = [_d(str(i)) for i in range(1, 11)]
    runtime = _FakeRuntime(closes=closes, points=[])
    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_AID: runtime})
    )
    snap = reader.paper_state(account_id=_AID, as_of=_NOW)
    assert all(v is None for v in snap.recent_sma20_series)
    assert all(v is None for v in snap.recent_sma50_series)


def test_open_positions_carry_sparkline_and_pnl_when_bars_available() -> None:
    """Each OpenPositionView SHALL carry the recent close series +
    latest close + unrealized P&L % so the dashboard can render
    an inline sparkline next to the position row."""
    from decimal import Decimal as _D

    from trading_system.models.instrument import InstrumentClass, Stock
    from trading_system.models.identifiers import InstrumentId
    from trading_system.models.money import Currency
    from trading_system.models.trading import Position, StopLoss

    class _FakePortfolio:
        def __init__(self, position: Position) -> None:
            self._pos = position

        def positions(self):
            return {self._pos.instrument.id: self._pos}

    stock = Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )
    pos = Position(
        instrument=stock,
        quantity=_D("5"),
        avg_price=_D("100"),
        opened_at=_NOW,
        stop_loss=StopLoss(price=_D("80")),
    )

    @dataclass(slots=True)
    class _PosRuntime(_FakeRuntime):
        _portfolio: _FakePortfolio | None = None

        @property
        def portfolio(self):
            return self._portfolio

    closes = [_d(str(i)) for i in range(1, 51)]  # last = 50
    runtime = _PosRuntime(closes=closes, points=[])
    runtime._portfolio = _FakePortfolio(pos)
    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_AID: runtime})
    )
    snap = reader.paper_state(account_id=_AID, as_of=_NOW)
    assert snap.open_positions_count == 1
    row = snap.open_positions[0]
    # Sparkline series + latest close populated.
    assert len(row.recent_close_series) == 30
    assert row.latest_close == _D("50")
    # Unrealized P&L % = (50 - 100) / 100 * 100 = -50.00
    assert row.unrealized_pnl_pct == _D("-50.00")


def test_reader_surfaces_reference_index_series_when_runtime_declares_one() -> None:
    """REQ_F_WEB2_010 — when the runtime carries a
    ``reference_index`` AND a market_data_provider that can
    serve its bars, the response SHALL carry the index symbol +
    close series for the dashboard's main chart."""
    from decimal import Decimal as _D
    from datetime import UTC as _UTC, datetime as _dt, timedelta as _td

    from trading_system.data.types import Bar
    from trading_system.models.identifiers import InstrumentId
    from trading_system.models.instrument import InstrumentClass, Stock
    from trading_system.models.money import Currency
    from trading_system.result import Ok

    @dataclass(slots=True)
    class _IdxProvider:
        bars_to_return: list

        def bars(self, instrument, timeframe, start, end):  # type: ignore[no-untyped-def]
            del instrument, timeframe, start, end
            return Ok(self.bars_to_return)

        def latest(self, instrument):  # type: ignore[no-untyped-def]
            del instrument
            return Ok(self.bars_to_return[-1])

    base = _dt(2026, 5, 1, tzinfo=_UTC)
    bars = [
        Bar(
            at=base + _td(days=i),
            open=_D(str(100 + i)),
            high=_D(str(101 + i)),
            low=_D(str(99 + i)),
            close=_D(str(100 + i)),
            volume=_D("1000"),
        )
        for i in range(5)
    ]
    idx = Stock(
        id=InstrumentId("^FCHI"),
        symbol="^FCHI",
        exchange="INDEX",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="INDEX_FCHI",
        sector="index",
        country="FR",
    )

    @dataclass(slots=True)
    class _IdxRuntime(_FakeRuntime):
        _reference_index: Stock | None = None
        _provider: _IdxProvider | None = None

        @property
        def reference_index(self):
            return self._reference_index

        @property
        def market_data_provider(self):
            return self._provider

    runtime = _IdxRuntime(closes=[_D("1")], points=[])
    runtime._reference_index = idx
    runtime._provider = _IdxProvider(bars_to_return=bars)

    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_AID: runtime})
    )
    snap = reader.paper_state(account_id=_AID, as_of=_NOW)
    assert snap.index_symbol == "^FCHI"
    assert len(snap.index_close_series) == 5
    assert snap.index_close_series[0] == _D("100")
    assert snap.index_close_series[-1] == _D("104")


def test_reader_returns_empty_index_when_runtime_has_no_reference() -> None:
    runtime = _FakeRuntime(closes=[_d("1")], points=[])
    reader = RuntimePaperStateReader(
        registry=_FakeRegistry(runtimes={_AID: runtime})
    )
    snap = reader.paper_state(account_id=_AID, as_of=_NOW)
    assert snap.index_symbol == ""
    assert snap.index_close_series == ()


def test_reader_returns_n_a_indicators_when_no_session() -> None:
    """No registered runtime SHALL still produce a valid snapshot
    with the documented sentinels — the dashboard panel renders
    the placeholder rows."""
    reader = RuntimePaperStateReader(registry=_FakeRegistry())
    snap = reader.paper_state(account_id=_AID, as_of=_NOW)
    assert snap.is_alive is False
    assert snap.regime == "n/a"
    assert snap.trend_signal == "n/a"
    assert snap.sma_20 is None
    assert snap.drawdown_pct is None
