"""Tests for ``trading_system.capital_flow.flow``.

REQ refs:
- REQ_F_CFL_001 — track every external injection.
- REQ_F_CFL_002 — performance series excludes injections.
- REQ_F_CFL_004 — backtest replay of an injection timeline never
  inflates returns.
- REQ_SDS_MOD_005 — equity-excl-injections is the canonical
  performance series.
- REQ_SDD_ALG_017 — observe re-sorts the timeline.

Covers TC_CFL_001..005:
- TC_CFL_001 — total capital = initial + sum of injections.
- TC_CFL_002 — equity-excluding-injections subtracts cumulative
  injections at each curve point.
- TC_CFL_004 — backtest replay of an injection timeline never inflates
  returns (verified at the engine layer; here we verify the strip).
- TC_CFL_005 — out-of-order injection insertion re-sorts by ``at``
  (REQ_SDD_ALG_017).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.capital_flow import CapitalFlow
from trading_system.models.flow import EquityPoint, Injection
from trading_system.models.money import Currency, Money

EUR = Currency.EUR
USD = Currency.USD


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _eq_point(at: datetime, after_tax: str, dd: str = "0") -> EquityPoint:
    return EquityPoint(
        at=at,
        equity_gross=_eur(after_tax),
        equity_after_tax=_eur(after_tax),
        drawdown_pct=Decimal(dd),
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_initial_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="initial must be > 0"):
            CapitalFlow(initial=_eur("0"))

    def test_initial_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="initial must be > 0"):
            CapitalFlow(initial=_eur("-1"))

    def test_currency_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"must share initial\.currency"):
            CapitalFlow(
                initial=_eur("1000"),
                injections=[Injection(amount=Money(Decimal("100"), USD), at=_ts(2026))],
            )

    def test_initial_only(self) -> None:
        cf = CapitalFlow(initial=_eur("1000"))
        assert cf.total_capital() == _eur("1000")
        assert cf.injections == []

    def test_currency_property(self) -> None:
        cf = CapitalFlow(initial=_eur("1000"))
        assert cf.currency is EUR


# ---------------------------------------------------------------------------
# total_capital — TC_CFL_001
# ---------------------------------------------------------------------------


class TestTotalCapital:
    def test_initial_only(self) -> None:
        cf = CapitalFlow(initial=_eur("1000"))
        assert cf.total_capital() == _eur("1000")

    def test_initial_plus_injections(self) -> None:
        cf = CapitalFlow(
            initial=_eur("1000"),
            injections=[
                Injection(amount=_eur("500"), at=_ts(2026, 6)),
                Injection(amount=_eur("300"), at=_ts(2026, 12)),
            ],
        )
        # 1000 + 500 + 300 = 1800
        assert cf.total_capital() == _eur("1800")


# ---------------------------------------------------------------------------
# cumulative_injected_at
# ---------------------------------------------------------------------------


class TestCumulativeInjectedAt:
    def test_before_any_injection(self) -> None:
        cf = CapitalFlow(
            initial=_eur("1000"),
            injections=[Injection(amount=_eur("500"), at=_ts(2026, 6))],
        )
        assert cf.cumulative_injected_at(_ts(2026, 1)) == _eur("0")

    def test_inclusive_at_injection_timestamp(self) -> None:
        ts = _ts(2026, 6)
        cf = CapitalFlow(
            initial=_eur("1000"),
            injections=[Injection(amount=_eur("500"), at=ts)],
        )
        assert cf.cumulative_injected_at(ts) == _eur("500")

    def test_running_total(self) -> None:
        cf = CapitalFlow(
            initial=_eur("1000"),
            injections=[
                Injection(amount=_eur("500"), at=_ts(2026, 6)),
                Injection(amount=_eur("300"), at=_ts(2026, 12)),
            ],
        )
        assert cf.cumulative_injected_at(_ts(2026, 6)) == _eur("500")
        assert cf.cumulative_injected_at(_ts(2026, 9)) == _eur("500")
        assert cf.cumulative_injected_at(_ts(2026, 12)) == _eur("800")
        assert cf.cumulative_injected_at(_ts(2027)) == _eur("800")


# ---------------------------------------------------------------------------
# equity_excl_injections — TC_CFL_002 / TC_CFL_004
# ---------------------------------------------------------------------------


class TestEquityExclInjections:
    def test_no_injections_passthrough(self) -> None:
        cf = CapitalFlow(initial=_eur("1000"))
        curve = [
            _eq_point(_ts(2026, 1), "1000"),
            _eq_point(_ts(2026, 6), "1100"),
            _eq_point(_ts(2027), "1200"),
        ]
        assert cf.equity_excl_injections(curve) == [
            Decimal("1000"),
            Decimal("1100"),
            Decimal("1200"),
        ]

    def test_subtracts_cumulative_injections(self) -> None:
        # Inject 500 at June, then equity climbs to 1700 by year-end.
        # Performance excluding injections: 1700 - 500 = 1200.
        cf = CapitalFlow(
            initial=_eur("1000"),
            injections=[Injection(amount=_eur("500"), at=_ts(2026, 6))],
        )
        curve = [
            _eq_point(_ts(2026, 1), "1000"),  # before injection
            _eq_point(_ts(2026, 6), "1500"),  # at injection (no PnL yet)
            _eq_point(_ts(2027), "1700"),  # +200 after injection
        ]
        assert cf.equity_excl_injections(curve) == [
            Decimal("1000"),
            Decimal("1000"),
            Decimal("1200"),
        ]

    def test_lump_sum_vs_dca_equal_total_yields_equal_strip(self) -> None:
        # REQ_TP_FIX_004: schedules of equal cumulative inflow yield the
        # same equity-excl-injections at the end-of-period point.
        ts_end = _ts(2027)
        # Lump sum: 600 at June.
        lump = CapitalFlow(
            initial=_eur("1000"),
            injections=[Injection(amount=_eur("600"), at=_ts(2026, 6))],
        )
        # DCA: 100/month for 6 months.
        dca = CapitalFlow(
            initial=_eur("1000"),
            injections=[
                Injection(amount=_eur("100"), at=_ts(2026, m)) for m in (7, 8, 9, 10, 11, 12)
            ],
        )
        curve = [_eq_point(ts_end, "2000")]
        assert lump.equity_excl_injections(curve) == dca.equity_excl_injections(curve)

    def test_currency_mismatch_panics(self) -> None:
        cf = CapitalFlow(initial=_eur("1000"))
        usd_curve = [
            EquityPoint(
                at=_ts(2026),
                equity_gross=Money(Decimal("1000"), USD),
                equity_after_tax=Money(Decimal("1000"), USD),
                drawdown_pct=Decimal(0),
            )
        ]
        with pytest.raises(ValueError, match="currency must match"):
            cf.equity_excl_injections(usd_curve)


# ---------------------------------------------------------------------------
# observe — TC_CFL_005 (REQ_SDD_ALG_017)
# ---------------------------------------------------------------------------


class TestObserve:
    def test_appends_in_order(self) -> None:
        cf = CapitalFlow(initial=_eur("1000"))
        cf.observe(Injection(amount=_eur("100"), at=_ts(2026, 1)))
        cf.observe(Injection(amount=_eur("200"), at=_ts(2026, 6)))
        assert [i.amount for i in cf.injections] == [_eur("100"), _eur("200")]

    def test_out_of_order_resorts(self) -> None:
        cf = CapitalFlow(initial=_eur("1000"))
        cf.observe(Injection(amount=_eur("200"), at=_ts(2026, 6)))
        cf.observe(Injection(amount=_eur("100"), at=_ts(2026, 1)))
        # Sorted ascending by .at — TC_CFL_005.
        assert [i.at for i in cf.injections] == [_ts(2026, 1), _ts(2026, 6)]

    def test_constructor_resorts_unsorted_input(self) -> None:
        cf = CapitalFlow(
            initial=_eur("1000"),
            injections=[
                Injection(amount=_eur("200"), at=_ts(2026, 6)),
                Injection(amount=_eur("100"), at=_ts(2026, 1)),
            ],
        )
        assert [i.at for i in cf.injections] == [_ts(2026, 1), _ts(2026, 6)]

    def test_currency_mismatch_panics(self) -> None:
        cf = CapitalFlow(initial=_eur("1000"))
        with pytest.raises(ValueError, match="must match"):
            cf.observe(Injection(amount=Money(Decimal("100"), USD), at=_ts(2026)))
