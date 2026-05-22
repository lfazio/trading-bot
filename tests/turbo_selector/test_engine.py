"""Tests for ``trading_system.turbo_selector.engine``.

Verifies the filter -> score -> select pipeline (REQ_F_TRB_001..006),
the Phase 1 "turbos disabled" gate (REQ_F_CAP_006), the threshold
gate (REQ_F_TRB_004 + REQ_SDS_MOD_007 — the selector SHALL emit
"no trade" when the best candidate's score is below the configured
threshold; the threshold is configurable via ``TurboSelectorConfig``),
and the Err-from-data drop policy.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from trading_system.data.types import Bar, Fundamentals, Timeframe
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import Instrument, InstrumentClass, Turbo
from trading_system.models.money import Currency
from trading_system.models.phase import (
    AllocationBucket,
    PhaseConstraints,
)
from trading_system.models.trading import Dividend
from trading_system.result import Err, Nothing, Ok, Result, Some
from trading_system.turbo_selector.config import TurboSelectorConfig
from trading_system.turbo_selector.engine import (
    TurboCandidate,
    TurboScore,
    select,
)

EUR = Currency.EUR


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def underlying(symbol: str = "AAPL") -> Instrument:
    return Instrument(
        id=InstrumentId(f"id-{symbol}"),
        symbol=symbol,
        exchange="NSQ",
        currency=EUR,
        cls=InstrumentClass.STOCK,
    )


def make_turbo(
    *,
    underlying_id: InstrumentId | None = None,
    leverage: str = "5",
    knockout: str = "90",
    spread_pct: str = "0.005",
) -> Turbo:
    return Turbo(
        id=InstrumentId(f"t-{leverage}-{knockout}"),
        symbol=f"T_{leverage}",
        exchange="EPA",
        currency=EUR,
        cls=InstrumentClass.TURBO,
        underlying=underlying_id or underlying().id,
        direction="LONG",
        leverage=Decimal(leverage),
        knockout=Decimal(knockout),
        spread_pct=Decimal(spread_pct),
    )


def constant_bars(
    *,
    price: str = "100",
    volume: str = "200000",
    count: int = 60,
    end_at: datetime = datetime(2026, 5, 1),
) -> list[Bar]:
    p = Decimal(price)
    v = Decimal(volume)
    bars: list[Bar] = []
    start = end_at - timedelta(days=count - 1)
    for i in range(count):
        bars.append(
            Bar(
                at=start + timedelta(days=i),
                open=p,
                high=p,
                low=p,
                close=p,
                volume=v,
            )
        )
    return bars


def oscillating_bars(
    *,
    base: str = "100",
    swing: str = "0.01",
    volume: str = "200000",
    count: int = 60,
    end_at: datetime = datetime(2026, 5, 1),
) -> list[Bar]:
    b = Decimal(base)
    s = Decimal(swing)
    v = Decimal(volume)
    bars: list[Bar] = []
    start = end_at - timedelta(days=count - 1)
    for i in range(count):
        price = b * (Decimal(1) + s) if i % 2 == 0 else b * (Decimal(1) - s)
        bars.append(
            Bar(
                at=start + timedelta(days=i),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=v,
            )
        )
    return bars


class StubMarketProvider:
    """Returns canned bars and the latest tick per underlying."""

    def __init__(self) -> None:
        self.bars_map: dict[InstrumentId, list[Bar]] = {}
        self.latest_map: dict[InstrumentId, Bar] = {}

    def bars(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> Result[list[Bar], str]:
        bars = self.bars_map.get(instrument.id)
        if bars is None:
            return Err("data:not_found")
        return Ok([b for b in bars if start <= b.at <= end])

    def latest(self, instrument: Instrument) -> Result[Bar, str]:
        bar = self.latest_map.get(instrument.id)
        return Ok(bar) if bar is not None else Err("data:not_found")

    def dividends(self, instrument: Instrument, year: int) -> Result[list[Dividend], str]:
        return Ok([])

    def fundamentals(self, instrument: Instrument) -> Result[Fundamentals, str]:
        return Err("data:not_found")


def make_phase_constraints(turbo_max: str = "0.05") -> PhaseConstraints:
    return PhaseConstraints(
        max_positions=6,
        max_trades_per_month=8,
        allocation_targets={
            AllocationBucket.STOCK: Decimal("0.95"),
            AllocationBucket.TURBO: Decimal(turbo_max),
            AllocationBucket.CASH: Decimal(1) - Decimal("0.95") - Decimal(turbo_max),
        },
        turbo_exposure_max=Decimal(turbo_max),
        risk_per_trade_band=(Decimal("0.01"), Decimal("0.02")),
        max_drawdown=Decimal("0.15"),
        portfolio_vol_cap=None,
    )


def populate_provider(
    provider: StubMarketProvider,
    instrument: Instrument,
    bars: list[Bar],
) -> None:
    provider.bars_map[instrument.id] = bars
    provider.latest_map[instrument.id] = bars[-1]


# ---------------------------------------------------------------------------
# TurboScore / ScoredTurbo construction
# ---------------------------------------------------------------------------


class TestResultTypes:
    def test_score_breakdown_valid(self) -> None:
        s = TurboScore(
            knockout_distance=Decimal("0.5"),
            leverage_efficiency=Decimal("0.5"),
            cost=Decimal("0.5"),
            expected_move_capture=Decimal("0.5"),
            total=Decimal("0.5"),
        )
        assert s.total == Decimal("0.5")

    @pytest.mark.parametrize("v", [Decimal("-0.01"), Decimal("1.01")])
    def test_score_out_of_range_rejected(self, v: Decimal) -> None:
        with pytest.raises(ValueError, match="TurboScore"):
            TurboScore(
                knockout_distance=v,
                leverage_efficiency=Decimal("0.5"),
                cost=Decimal("0.5"),
                expected_move_capture=Decimal("0.5"),
                total=Decimal("0.5"),
            )


# ---------------------------------------------------------------------------
# select() — phase gate
# ---------------------------------------------------------------------------


class TestPhaseGate:
    def test_phase1_disabled_returns_nothing(self) -> None:
        # turbo_exposure_max == 0 => REQ_F_CAP_006 trigger.
        provider = StubMarketProvider()
        und = underlying()
        populate_provider(provider, und, constant_bars())
        candidate = TurboCandidate(
            turbo=make_turbo(leverage="3"),
            underlying=und,
        )
        result = select(
            [candidate],
            provider,
            make_phase_constraints(turbo_max="0"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        assert result == Nothing()


# ---------------------------------------------------------------------------
# select() — filter rejections
# ---------------------------------------------------------------------------


class TestFilterRejections:
    def _setup(
        self, turbo: Turbo, bars: list[Bar] | None = None
    ) -> tuple[StubMarketProvider, TurboCandidate]:
        provider = StubMarketProvider()
        und = underlying()
        populate_provider(provider, und, bars or constant_bars())
        return provider, TurboCandidate(turbo=turbo, underlying=und)

    def test_knockout_too_close_rejected(self) -> None:
        # underlying 100, knockout 99 -> distance 0.01 < 0.05.
        provider, candidate = self._setup(make_turbo(knockout="99"))
        result = select(
            [candidate],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        assert result == Nothing()

    def test_spread_too_wide_rejected(self) -> None:
        provider, candidate = self._setup(make_turbo(spread_pct="0.020"))
        result = select(
            [candidate],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        assert result == Nothing()

    def test_leverage_above_phase_cap_rejected(self) -> None:
        # phase cap 0.05 * 100 = 5x; leverage 10 > 5.
        provider, candidate = self._setup(make_turbo(leverage="10"))
        result = select(
            [candidate],
            provider,
            make_phase_constraints(turbo_max="0.05"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        assert result == Nothing()

    def test_low_liquidity_rejected(self) -> None:
        provider, candidate = self._setup(
            make_turbo(),
            bars=constant_bars(volume="1000"),  # below min_liquidity 100k
        )
        result = select(
            [candidate],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        assert result == Nothing()

    def test_extreme_volatility_rejected(self) -> None:
        # ±20% daily swings => annualized vol massively above 0.50.
        provider, candidate = self._setup(
            make_turbo(),
            bars=oscillating_bars(swing="0.20"),
        )
        result = select(
            [candidate],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        assert result == Nothing()


# ---------------------------------------------------------------------------
# select() — happy paths
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_passing_candidate_returned(self) -> None:
        provider = StubMarketProvider()
        und = underlying()
        populate_provider(provider, und, oscillating_bars(swing="0.01"))
        candidate = TurboCandidate(
            turbo=make_turbo(leverage="5", knockout="90", spread_pct="0.005"),
            underlying=und,
        )
        result = select(
            [candidate],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        match result:
            case Some(scored):
                assert scored.candidate.turbo == candidate.turbo
                assert scored.score.total >= TurboSelectorConfig().threshold
            case Nothing():
                pytest.fail("expected Some")

    def test_picks_best_among_eligible(self) -> None:
        provider = StubMarketProvider()
        und = underlying()
        populate_provider(provider, und, oscillating_bars(swing="0.01"))
        # Two eligible candidates; the second has higher leverage =>
        # higher move-capture score => higher total.
        a = TurboCandidate(
            turbo=make_turbo(leverage="3", knockout="90", spread_pct="0.005"),
            underlying=und,
        )
        b = TurboCandidate(
            turbo=make_turbo(leverage="10", knockout="80", spread_pct="0.002"),
            underlying=und,
        )
        result = select(
            [a, b],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        match result:
            case Some(scored):
                assert scored.candidate.turbo.leverage == Decimal("10")
            case Nothing():
                pytest.fail("expected Some")

    def test_below_threshold_returns_nothing(self) -> None:
        # Push the threshold above 1.0 so nothing can satisfy it.
        provider = StubMarketProvider()
        und = underlying()
        populate_provider(provider, und, oscillating_bars(swing="0.01"))
        candidate = TurboCandidate(turbo=make_turbo(), underlying=und)
        cfg = TurboSelectorConfig(threshold=Decimal("1.0"))
        result = select(
            [candidate],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            cfg,
            at=datetime(2026, 5, 1),
        )
        assert result == Nothing()


# ---------------------------------------------------------------------------
# select() — Err-from-data drop policy
# ---------------------------------------------------------------------------


class TestErrDropPolicy:
    def test_missing_data_drops_candidate(self) -> None:
        # Provider has no entry for the underlying => candidate skipped.
        provider = StubMarketProvider()  # empty
        candidate = TurboCandidate(turbo=make_turbo(), underlying=underlying())
        result = select(
            [candidate],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        assert result == Nothing()

    def test_missing_data_for_one_falls_through_to_next(self) -> None:
        provider = StubMarketProvider()
        # Two underlyings; only the second has data.
        u1 = underlying("AAA")
        u2 = underlying("BBB")
        populate_provider(provider, u2, oscillating_bars(swing="0.01"))
        a = TurboCandidate(
            turbo=make_turbo(underlying_id=u1.id),
            underlying=u1,
        )
        b = TurboCandidate(
            turbo=make_turbo(underlying_id=u2.id, leverage="5", knockout="80", spread_pct="0.005"),
            underlying=u2,
        )
        result = select(
            [a, b],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        match result:
            case Some(scored):
                assert scored.candidate.underlying.id == u2.id
            case Nothing():
                pytest.fail("expected Some — second candidate should pass")


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_no_candidates_returns_nothing(self) -> None:
        provider = StubMarketProvider()
        result = select(
            [],
            provider,
            make_phase_constraints(turbo_max="0.20"),
            TurboSelectorConfig(),
            at=datetime(2026, 5, 1),
        )
        assert result == Nothing()
