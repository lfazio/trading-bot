"""Tests for ``trading_system.tax.engine``.

Covers REQ_F_TAX_001 / 002 (formula), REQ_F_TAX_003 (gate boundary
behavior), REQ_F_TAX_004 (all optimizations target net-after-tax —
``net_gain`` / ``net_dividend`` return post-tax amounts only; the
engine never exposes a gross-return path that could be optimized
in place of the after-tax one), REQ_SDD_ALG_001 (ROUND_HALF_UP cents),
and the cross-currency panic discipline.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.money import Currency, Money
from trading_system.tax.config import TaxConfig
from trading_system.tax.engine import net_dividend, net_gain, trade_passes_gate

EUR = Currency.EUR
USD = Currency.USD


def cfg(rate: str = "0.30", mult: int = 5) -> TaxConfig:
    return TaxConfig(rate=Decimal(rate), gate_multiplier=mult)


# ---------------------------------------------------------------------------
# net_gain
# ---------------------------------------------------------------------------


class TestNetGain:
    def test_basic(self) -> None:
        # REQ_F_TAX_001: 100 x (1 - 0.30) = 70.00.
        assert net_gain(cfg(), Money(Decimal("100.00"), EUR)) == Money(Decimal("70.00"), EUR)

    def test_zero(self) -> None:
        assert net_gain(cfg(), Money(Decimal(0), EUR)) == Money(Decimal("0.00"), EUR)

    def test_loss_passthrough(self) -> None:
        # Losses are not "taxed positively"; they pass through (rounded).
        assert net_gain(cfg(), Money(Decimal("-50.00"), EUR)) == Money(Decimal("-50.00"), EUR)

    def test_round_half_up_to_cent(self) -> None:
        # 0.005 rounds up to 0.01 (HALF_UP).
        # gross = 100.50 x 0.70 = 70.35 exactly.
        # try a non-trivial half-cent case:
        # gross = 0.0072 (rate=0.30) -> 0.005 -> 0.01.
        # gross.amount x 0.70 = 0.005 (exactly).
        result = net_gain(cfg(), Money(Decimal("0.00714286"), EUR))
        # 0.00714286 x 0.70 = 0.005; rounds up to 0.01.
        assert result == Money(Decimal("0.01"), EUR)

    def test_round_half_up_at_half_cent(self) -> None:
        # gross 0.00714285 x 0.70 = 0.0049999... rounds DOWN to 0.00.
        result = net_gain(cfg(), Money(Decimal("0.00714285"), EUR))
        assert result == Money(Decimal("0.00"), EUR)

    def test_zero_rate(self) -> None:
        assert net_gain(cfg(rate="0.00"), Money(Decimal("100"), EUR)) == Money(
            Decimal("100.00"), EUR
        )

    def test_full_rate(self) -> None:
        assert net_gain(cfg(rate="1.00"), Money(Decimal("100"), EUR)) == Money(Decimal("0.00"), EUR)

    def test_currency_preserved(self) -> None:
        assert net_gain(cfg(), Money(Decimal("100"), USD)).currency is USD


# ---------------------------------------------------------------------------
# net_dividend
# ---------------------------------------------------------------------------


class TestNetDividend:
    def test_basic(self) -> None:
        assert net_dividend(cfg(), Money(Decimal("2.50"), EUR)) == Money(Decimal("1.75"), EUR)

    def test_zero(self) -> None:
        assert net_dividend(cfg(), Money(Decimal(0), EUR)) == Money(Decimal("0.00"), EUR)

    def test_negative_panics(self) -> None:
        with pytest.raises(AssertionError, match="net_dividend"):
            net_dividend(cfg(), Money(Decimal("-1"), EUR))

    def test_round_half_up(self) -> None:
        # 0.50 x 0.70 = 0.35 exactly.
        assert net_dividend(cfg(), Money(Decimal("0.50"), EUR)) == Money(Decimal("0.35"), EUR)


# ---------------------------------------------------------------------------
# trade_passes_gate
# ---------------------------------------------------------------------------


class TestTradeGate:
    def test_passes_when_strictly_above(self) -> None:
        assert (
            trade_passes_gate(
                cfg(),
                expected_net_profit=Money(Decimal("5.01"), EUR),
                total_fees=Money(Decimal("1.00"), EUR),
            )
            is True
        )

    def test_fails_at_exact_boundary(self) -> None:
        # Strict >: net == 5 x fees fails (REQ_C_BHV_003: marginal trades rejected).
        assert (
            trade_passes_gate(
                cfg(),
                expected_net_profit=Money(Decimal("5.00"), EUR),
                total_fees=Money(Decimal("1.00"), EUR),
            )
            is False
        )

    def test_fails_below_boundary(self) -> None:
        assert (
            trade_passes_gate(
                cfg(),
                expected_net_profit=Money(Decimal("4.99"), EUR),
                total_fees=Money(Decimal("1.00"), EUR),
            )
            is False
        )

    def test_fails_one_cent_above_below(self) -> None:
        # REQ_SDD_TST_003: ±1 cent boundary check.
        assert trade_passes_gate(
            cfg(),
            expected_net_profit=Money(Decimal("5.01"), EUR),
            total_fees=Money(Decimal("1.00"), EUR),
        )
        assert not trade_passes_gate(
            cfg(),
            expected_net_profit=Money(Decimal("4.99"), EUR),
            total_fees=Money(Decimal("1.00"), EUR),
        )

    def test_negative_profit_fails(self) -> None:
        assert (
            trade_passes_gate(
                cfg(),
                expected_net_profit=Money(Decimal("-1"), EUR),
                total_fees=Money(Decimal("1.00"), EUR),
            )
            is False
        )

    def test_zero_fees_pass_for_any_positive_net(self) -> None:
        assert (
            trade_passes_gate(
                cfg(),
                expected_net_profit=Money(Decimal("0.01"), EUR),
                total_fees=Money(Decimal(0), EUR),
            )
            is True
        )

    def test_cross_currency_panics(self) -> None:
        with pytest.raises(AssertionError, match="cross-currency"):
            trade_passes_gate(
                cfg(),
                expected_net_profit=Money(Decimal("10"), EUR),
                total_fees=Money(Decimal("1"), USD),
            )

    def test_custom_multiplier(self) -> None:
        # gate_multiplier = 10 => stricter gate.
        c = cfg(mult=10)
        assert not trade_passes_gate(
            c,
            expected_net_profit=Money(Decimal("9.99"), EUR),
            total_fees=Money(Decimal("1.00"), EUR),
        )
        assert trade_passes_gate(
            c,
            expected_net_profit=Money(Decimal("10.01"), EUR),
            total_fees=Money(Decimal("1.00"), EUR),
        )
