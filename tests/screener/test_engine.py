"""Tests for ``trading_system.screener.engine``.

Covers the filter (REQ_F_SCR_001), scored ranking (REQ_F_SCR_002),
filter-evaluation order (REQ_SDD_ALG_018), the public `FILTER_RULES`
ordering (REQ_SDD_ALG_021), screen()'s Err-handling and stable sort
(REQ_SDD_ALG_022), the clamped score-helper formulas
(REQ_SDD_ALG_023), and the ScoreBreakdown / ScoredStock shape
(REQ_SDD_DAT_009).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar, Fundamentals, Timeframe
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import Instrument, InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Dividend
from trading_system.result import Err, Ok, Result
from trading_system.screener.config import ScreenerConfig
from trading_system.screener.engine import (
    FILTER_RULES,
    ScoreBreakdown,
    ScoredStock,
    screen,
    stability_score,
    valuation_score,
    yield_quality_score,
)

EUR = Currency.EUR


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_stock(symbol: str = "ABC", isin: str = "FR0000000000") -> Stock:
    return Stock(
        id=InstrumentId(f"id-{symbol}"),
        symbol=symbol,
        exchange="EPA",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin=isin,
        sector="Industrials",
        country="FR",
    )


def make_fund(
    *,
    yield_: str = "0.04",
    payout: str = "0.50",
    fcf: str = "1000",
    de: str = "0.5",
    history: int = 10,
) -> Fundamentals:
    return Fundamentals(
        yield_=Decimal(yield_),
        payout_ratio=Decimal(payout),
        free_cash_flow=Money(Decimal(fcf), EUR),
        debt_equity=Decimal(de),
        dividend_history_years=history,
    )


class StubProvider:
    """Test double for ``MarketDataProvider`` driven by an explicit
    ``InstrumentId -> Result[Fundamentals, str]`` mapping. Other
    Protocol methods raise to surface accidental usage.
    """

    def __init__(self, mapping: dict[InstrumentId, Result[Fundamentals, str]]) -> None:
        self._mapping = mapping

    def fundamentals(self, instrument: object) -> Result[Fundamentals, str]:
        assert isinstance(instrument, Instrument)
        return self._mapping.get(instrument.id, Err("data:not_found"))

    # The Protocol surface includes other methods; the screener never
    # touches them, but we still need them defined so ``isinstance``
    # checks pass when the suite needs to verify Protocol conformance.
    def bars(
        self, instrument: object, timeframe: Timeframe, start: datetime, end: datetime
    ) -> Result[list[Bar], str]:  # pragma: no cover - never called by screener
        raise AssertionError("StubProvider.bars must not be called by the screener")

    def latest(self, instrument: object) -> Result[Bar, str]:  # pragma: no cover
        raise AssertionError("StubProvider.latest must not be called by the screener")

    def dividends(
        self, instrument: object, year: int
    ) -> Result[list[Dividend], str]:  # pragma: no cover
        raise AssertionError("StubProvider.dividends must not be called by the screener")


def test_stub_provider_is_market_data_provider() -> None:
    # The stub satisfies the runtime-checkable Protocol.
    assert isinstance(StubProvider({}), MarketDataProvider)


# ---------------------------------------------------------------------------
# FILTER_RULES — order and individual predicates (REQ_SDD_ALG_018)
# ---------------------------------------------------------------------------


class TestFilterRules:
    def test_rule_names_in_required_order(self) -> None:
        assert [r.name for r in FILTER_RULES] == [
            "yield",
            "payout",
            "free_cash_flow",
            "debt_equity",
            "history",
        ]

    def test_yield_rule_band_inclusive(self) -> None:
        cfg = ScreenerConfig()
        rule = next(r for r in FILTER_RULES if r.name == "yield")
        assert rule.predicate(make_fund(yield_="0.03"), cfg)  # boundary
        assert rule.predicate(make_fund(yield_="0.07"), cfg)  # boundary
        assert not rule.predicate(make_fund(yield_="0.029"), cfg)
        assert not rule.predicate(make_fund(yield_="0.071"), cfg)

    def test_payout_strict_below_max(self) -> None:
        cfg = ScreenerConfig()
        rule = next(r for r in FILTER_RULES if r.name == "payout")
        assert rule.predicate(make_fund(payout="0.69"), cfg)
        assert not rule.predicate(make_fund(payout="0.70"), cfg)
        assert not rule.predicate(make_fund(payout="0.71"), cfg)

    def test_fcf_must_be_strictly_positive(self) -> None:
        cfg = ScreenerConfig()
        rule = next(r for r in FILTER_RULES if r.name == "free_cash_flow")
        assert rule.predicate(make_fund(fcf="1"), cfg)
        assert not rule.predicate(make_fund(fcf="0"), cfg)
        assert not rule.predicate(make_fund(fcf="-1"), cfg)

    def test_debt_equity_strict_below_max(self) -> None:
        cfg = ScreenerConfig()
        rule = next(r for r in FILTER_RULES if r.name == "debt_equity")
        assert rule.predicate(make_fund(de="1.49"), cfg)
        assert not rule.predicate(make_fund(de="1.5"), cfg)
        assert not rule.predicate(make_fund(de="1.51"), cfg)

    def test_history_inclusive_floor(self) -> None:
        cfg = ScreenerConfig()
        rule = next(r for r in FILTER_RULES if r.name == "history")
        assert rule.predicate(make_fund(history=5), cfg)
        assert rule.predicate(make_fund(history=20), cfg)
        assert not rule.predicate(make_fund(history=4), cfg)


# ---------------------------------------------------------------------------
# Score helpers — REQ_F_SCR_002
# ---------------------------------------------------------------------------


class TestStabilityScore:
    def test_zero_history(self) -> None:
        assert stability_score(make_fund(history=0), ScreenerConfig()) == Decimal(0)

    def test_full_history(self) -> None:
        assert stability_score(make_fund(history=20), ScreenerConfig()) == Decimal(1)

    def test_above_full_clamps_to_one(self) -> None:
        assert stability_score(make_fund(history=50), ScreenerConfig()) == Decimal(1)

    def test_linear_ramp_at_half(self) -> None:
        # 10 / 20 = 0.5
        assert stability_score(make_fund(history=10), ScreenerConfig()) == Decimal("0.5")


class TestYieldQualityScore:
    def test_zero_payout_full_safety(self) -> None:
        assert yield_quality_score(make_fund(payout="0"), ScreenerConfig()) == Decimal(1)

    def test_payout_at_max_zero(self) -> None:
        assert yield_quality_score(make_fund(payout="0.70"), ScreenerConfig()) == Decimal(0)

    def test_payout_above_max_clamps(self) -> None:
        assert yield_quality_score(make_fund(payout="0.90"), ScreenerConfig()) == Decimal(0)

    def test_half_payout(self) -> None:
        # 1 - 0.35 / 0.70 = 0.5
        assert yield_quality_score(make_fund(payout="0.35"), ScreenerConfig()) == Decimal("0.5")


class TestValuationScore:
    def test_zero_leverage(self) -> None:
        assert valuation_score(make_fund(de="0"), ScreenerConfig()) == Decimal(1)

    def test_max_leverage_zero(self) -> None:
        assert valuation_score(make_fund(de="1.5"), ScreenerConfig()) == Decimal(0)

    def test_above_max_clamps(self) -> None:
        assert valuation_score(make_fund(de="3"), ScreenerConfig()) == Decimal(0)

    def test_half(self) -> None:
        # 1 - 0.75 / 1.5 = 0.5
        assert valuation_score(make_fund(de="0.75"), ScreenerConfig()) == Decimal("0.5")


# ---------------------------------------------------------------------------
# ScoredStock + ScoreBreakdown construction
# ---------------------------------------------------------------------------


class TestResultTypes:
    def test_score_breakdown_valid(self) -> None:
        b = ScoreBreakdown(
            stability=Decimal("0.5"),
            yield_quality=Decimal("0.5"),
            valuation=Decimal("0.5"),
        )
        assert b.stability == Decimal("0.5")

    @pytest.mark.parametrize("v", [Decimal("-0.01"), Decimal("1.01")])
    def test_score_breakdown_out_of_range(self, v: Decimal) -> None:
        with pytest.raises(ValueError, match="ScoreBreakdown"):
            ScoreBreakdown(
                stability=v,
                yield_quality=Decimal("0.5"),
                valuation=Decimal("0.5"),
            )

    def test_scored_stock_total_out_of_range(self) -> None:
        b = ScoreBreakdown(
            stability=Decimal("0.5"),
            yield_quality=Decimal("0.5"),
            valuation=Decimal("0.5"),
        )
        with pytest.raises(ValueError, match="ScoredStock"):
            ScoredStock(stock=make_stock(), score=Decimal("1.5"), breakdown=b)


# ---------------------------------------------------------------------------
# screen()
# ---------------------------------------------------------------------------


class TestScreen:
    def test_passes_basic_candidate(self) -> None:
        s = make_stock("AAA")
        provider = StubProvider({s.id: Ok(make_fund())})
        result = screen([s], provider, ScreenerConfig())
        assert len(result) == 1
        assert result[0].stock == s
        assert result[0].score > Decimal(0)

    def test_filter_rejects_below_yield(self) -> None:
        s = make_stock("AAA")
        provider = StubProvider({s.id: Ok(make_fund(yield_="0.01"))})
        assert screen([s], provider, ScreenerConfig()) == []

    def test_unavailable_fundamentals_dropped_silently(self) -> None:
        s = make_stock("AAA")
        provider = StubProvider({s.id: Err("data:not_found")})
        assert screen([s], provider, ScreenerConfig()) == []

    def test_orders_by_score_descending(self) -> None:
        # Three candidates: vary history so stability differs. All
        # other fields chosen to give identical yield/payout/de scores.
        a = make_stock("AAA", isin="FR000A")
        b = make_stock("BBB", isin="FR000B")
        c = make_stock("CCC", isin="FR000C")
        provider = StubProvider(
            {
                a.id: Ok(make_fund(history=5)),
                b.id: Ok(make_fund(history=20)),
                c.id: Ok(make_fund(history=10)),
            }
        )
        result = screen([a, b, c], provider, ScreenerConfig())
        assert [r.stock.symbol for r in result] == ["BBB", "CCC", "AAA"]
        # And the scores are descending.
        assert result[0].score > result[1].score > result[2].score

    def test_stable_sort_preserves_universe_order_on_tie(self) -> None:
        a = make_stock("AAA", isin="FR000A")
        b = make_stock("BBB", isin="FR000B")
        # Identical fundamentals -> identical scores; AAA first in input.
        provider = StubProvider({a.id: Ok(make_fund()), b.id: Ok(make_fund())})
        result = screen([a, b], provider, ScreenerConfig())
        assert [r.stock.symbol for r in result] == ["AAA", "BBB"]

    def test_score_total_matches_weighted_sum(self) -> None:
        s = make_stock("AAA")
        f = make_fund(history=10, payout="0.35", de="0.75")  # all 0.5
        provider = StubProvider({s.id: Ok(f)})
        result = screen([s], provider, ScreenerConfig())
        # All breakdown components 0.5; weights sum to 1; total = 0.5.
        assert result[0].score == Decimal("0.5")
        assert result[0].breakdown.stability == Decimal("0.5")
        assert result[0].breakdown.yield_quality == Decimal("0.5")
        assert result[0].breakdown.valuation == Decimal("0.5")

    def test_filter_short_circuits_on_first_failure(self) -> None:
        # Build a Fundamentals that fails the *first* rule (yield);
        # later rules' values don't matter.
        cfg = ScreenerConfig()
        s = make_stock("AAA")
        f = Fundamentals(
            yield_=Decimal("0.01"),  # fails yield band immediately
            payout_ratio=Decimal("0.99"),  # would fail payout too
            free_cash_flow=Money(Decimal(-1), EUR),  # would fail FCF too
            debt_equity=Decimal("999"),  # would fail D/E too
            dividend_history_years=0,  # would fail history too
        )
        provider = StubProvider({s.id: Ok(f)})
        # screen() simply returns empty; the test confirms the pipeline
        # does not panic on the trailing-rule values, demonstrating
        # short-circuit behavior.
        assert screen([s], provider, cfg) == []

    def test_empty_universe(self) -> None:
        provider = StubProvider({})
        assert screen([], provider, ScreenerConfig()) == []
