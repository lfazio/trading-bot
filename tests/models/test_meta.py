"""Tests for ``trading_system.models.meta``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import InstrumentId, StrategyId
from trading_system.models.instrument import Instrument, InstrumentClass
from trading_system.models.meta import (
    ImprovementReport,
    TradeProposal,
    ValidationResult,
)
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side, StopLoss

EUR = Currency.EUR


def stock() -> Instrument:
    return Instrument(
        id=InstrumentId("ABC"),
        symbol="ABC",
        exchange="EPA",
        currency=EUR,
        cls=InstrumentClass.STOCK,
    )


def proposal(**overrides: object) -> TradeProposal:
    base: dict[str, object] = {
        "instrument": stock(),
        "side": Side.BUY,
        "size_pct_of_capital": Decimal("0.05"),
        "expected_net_profit": Money(Decimal("12.50"), EUR),
        "expected_fees": Money(Decimal("0.50"), EUR),
        "stop_loss": StopLoss(price=Decimal("90")),
        "source_strategy": StrategyId("core_v1"),
    }
    base.update(overrides)
    return TradeProposal(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TradeProposal
# ---------------------------------------------------------------------------


class TestTradeProposal:
    def test_valid(self) -> None:
        p = proposal()
        assert p.size_pct_of_capital == Decimal("0.05")

    @pytest.mark.parametrize("size", [Decimal(0), Decimal("-0.01"), Decimal("1.01")])
    def test_invalid_size_rejected(self, size: Decimal) -> None:
        with pytest.raises(ValueError, match="size_pct_of_capital"):
            proposal(size_pct_of_capital=size)

    def test_negative_fees_rejected(self) -> None:
        with pytest.raises(ValueError, match="expected_fees must be >= 0"):
            proposal(expected_fees=Money(Decimal("-0.01"), EUR))

    def test_currency_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="must share a currency"):
            proposal(expected_fees=Money(Decimal("0.50"), Currency.USD))


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_accept_factory(self) -> None:
        v = ValidationResult.accept()
        assert v.passed is True
        assert v.reasons == ()

    def test_reject_factory(self) -> None:
        v = ValidationResult.reject("size", "regime")
        assert v.passed is False
        assert v.reasons == ("size", "regime")

    def test_passed_with_reasons_rejected(self) -> None:
        with pytest.raises(ValueError, match="passed=True must carry no reasons"):
            ValidationResult(passed=True, reasons=("foo",))

    def test_failed_without_reasons_rejected(self) -> None:
        with pytest.raises(ValueError, match="passed=False must carry at least one reason"):
            ValidationResult(passed=False, reasons=())

    def test_reject_factory_requires_reason(self) -> None:
        with pytest.raises(ValueError, match="requires at least one reason"):
            ValidationResult.reject()


# ---------------------------------------------------------------------------
# ImprovementReport
# ---------------------------------------------------------------------------


def report(**overrides: object) -> ImprovementReport:
    base: dict[str, object] = {
        "cycle_id": "cycle-001",
        "best_strategy_id": StrategyId("v13"),
        "deltas": {"return": Decimal("0.012")},
        "risk_assessment": "ok",
        "rejected": (StrategyId("v12"),),
        "rejection_reasons": {StrategyId("v12"): "dd_breach"},
        "generated_at": datetime(2026, 5, 1),
    }
    base.update(overrides)
    return ImprovementReport(**base)  # type: ignore[arg-type]


class TestImprovementReport:
    def test_valid_with_accepted(self) -> None:
        r = report()
        assert r.best_strategy_id == "v13"

    def test_valid_only_rejected(self) -> None:
        r = report(best_strategy_id=None)
        assert r.best_strategy_id is None
        assert len(r.rejected) == 1

    def test_empty_cycle_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="cycle_id must be non-empty"):
            report(cycle_id="")

    def test_rejected_keys_must_match_reasons(self) -> None:
        with pytest.raises(ValueError, match="missing reasons"):
            report(rejected=(StrategyId("v12"), StrategyId("v11")))
        with pytest.raises(ValueError, match="extra reasons"):
            report(rejection_reasons={StrategyId("v12"): "ok", StrategyId("vNA"): "?"})

    def test_no_accepted_no_rejected_invalid(self) -> None:
        with pytest.raises(ValueError, match="accepted best_strategy_id or at least one rejection"):
            report(best_strategy_id=None, rejected=(), rejection_reasons={})
