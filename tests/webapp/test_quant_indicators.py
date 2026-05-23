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
