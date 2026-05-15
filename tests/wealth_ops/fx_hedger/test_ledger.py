"""Tests for ``trading_system.wealth_ops.fx_hedger.ledger``.

Covers TC_FXH_007 (open/close happy path) + TC_FXH_008 (error cases)
+ TC_FXH_009 (mark formula) + TC_FXH_010 (tax treatment).

REQ refs: REQ_F_FXH_005, REQ_F_FXH_006, REQ_SDD_FXH_004,
REQ_SDD_FXH_005, REQ_C_TAX_001.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.models.money import Currency, Money
from trading_system.result import Err, Ok
from trading_system.wealth_ops.fx_hedger.forward import (
    FXForwardState,
    ForwardId,
    HedgeProposal,
)
from trading_system.wealth_ops.fx_hedger.ledger import FXHedgeLedger, mark


_AT = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)


def _proposal(notional: str = "16000", ratio: str = "1.0") -> HedgeProposal:
    return HedgeProposal(
        currency=Currency.USD,
        base_currency=Currency.EUR,
        exposure_amount=Money(Decimal(notional), Currency.EUR),
        target_hedge_ratio=Decimal(ratio),
        decided_at=_AT,
    )


# ---------------------------------------------------------------------------
# TC_FXH_007 — open + close happy path
# ---------------------------------------------------------------------------


def test_open_appends_forward_in_open_state() -> None:
    ledger = FXHedgeLedger()
    forward = ledger.open(
        _proposal(notional="20000", ratio="0.80"),
        entry_fx_rate=Decimal("1.10"),
        opened_at=_AT,
    )
    assert forward.state is FXForwardState.OPEN
    assert forward.entry_fx_rate == Decimal("1.10")
    assert forward.notional == Money(Decimal("16000.00"), Currency.EUR)
    assert forward.id == ForwardId("fwd-1")
    assert ledger.open_forwards() == (forward,)
    assert ledger.closed_forwards() == ()


def test_close_returns_realised_pnl_and_marks_state() -> None:
    ledger = FXHedgeLedger()
    forward = ledger.open(
        _proposal(notional="1000", ratio="1.0"),
        entry_fx_rate=Decimal("1.10"),
        opened_at=_AT,
    )
    res = ledger.close(forward.id, exit_fx_rate=Decimal("1.05"), closed_at=_AT)
    # PnL = notional × (exit / entry - 1) = 1000 × (1.05/1.10 - 1)
    expected = Decimal("1000") * (Decimal("1.05") / Decimal("1.10") - Decimal(1))
    pnl = res.unwrap()
    assert pnl.amount == expected
    assert pnl.currency is Currency.EUR
    # State updated to CLOSED.
    closed = ledger.closed_forwards()
    assert len(closed) == 1
    assert closed[0].state is FXForwardState.CLOSED
    assert closed[0].exit_fx_rate == Decimal("1.05")
    assert ledger.open_forwards() == ()


def test_monotonic_forward_ids() -> None:
    ledger = FXHedgeLedger()
    a = ledger.open(_proposal(), entry_fx_rate=Decimal("1.10"), opened_at=_AT)
    b = ledger.open(_proposal(), entry_fx_rate=Decimal("1.10"), opened_at=_AT)
    c = ledger.open(_proposal(), entry_fx_rate=Decimal("1.10"), opened_at=_AT)
    assert a.id == ForwardId("fwd-1")
    assert b.id == ForwardId("fwd-2")
    assert c.id == ForwardId("fwd-3")


# ---------------------------------------------------------------------------
# TC_FXH_008 — error cases
# ---------------------------------------------------------------------------


def test_close_unknown_id_returns_not_found_err() -> None:
    ledger = FXHedgeLedger()
    match ledger.close(
        ForwardId("fwd-ghost"),
        exit_fx_rate=Decimal("1.05"),
        closed_at=_AT,
    ):
        case Err(reason):
            assert reason == "fxh:not_found:fwd-ghost"
        case Ok(_):
            raise AssertionError("expected Err on unknown id")


def test_close_already_closed_returns_already_closed_err() -> None:
    ledger = FXHedgeLedger()
    forward = ledger.open(
        _proposal(),
        entry_fx_rate=Decimal("1.10"),
        opened_at=_AT,
    )
    ledger.close(forward.id, exit_fx_rate=Decimal("1.05"), closed_at=_AT)
    match ledger.close(
        forward.id, exit_fx_rate=Decimal("1.05"), closed_at=_AT
    ):
        case Err(reason):
            assert reason == "fxh:already_closed:fwd-1"
        case Ok(_):
            raise AssertionError("expected Err on double-close")


def test_failed_close_does_not_mutate_ledger() -> None:
    ledger = FXHedgeLedger()
    forward = ledger.open(_proposal(), entry_fx_rate=Decimal("1.10"), opened_at=_AT)
    snapshot_before = ledger.all_forwards()
    ledger.close(ForwardId("fwd-ghost"), exit_fx_rate=Decimal("1.05"), closed_at=_AT)
    snapshot_after = ledger.all_forwards()
    assert snapshot_before == snapshot_after


# ---------------------------------------------------------------------------
# TC_FXH_009 — mark formula
# ---------------------------------------------------------------------------


def test_mark_formula_is_deterministic() -> None:
    ledger = FXHedgeLedger()
    forward = ledger.open(_proposal(notional="1000"), entry_fx_rate=Decimal("1.10"), opened_at=_AT)
    a = mark(forward, Decimal("1.15"))
    b = mark(forward, Decimal("1.15"))
    assert a == b
    assert a.currency is Currency.EUR
    expected = Decimal("1000") * (Decimal("1.15") / Decimal("1.10") - Decimal(1))
    assert a.amount == expected


def test_mark_rejects_non_positive_rate() -> None:
    ledger = FXHedgeLedger()
    forward = ledger.open(_proposal(), entry_fx_rate=Decimal("1.10"), opened_at=_AT)
    import pytest as _pytest

    with _pytest.raises(ValueError, match="current_fx_rate"):
        mark(forward, Decimal("0"))


# ---------------------------------------------------------------------------
# TC_FXH_010 — tax treatment
# ---------------------------------------------------------------------------


def test_net_positive_ledger_taxed_at_thirty_percent() -> None:
    ledger = FXHedgeLedger()
    # Two forwards — one gain, one loss; net positive.
    f1 = ledger.open(_proposal(notional="1000"), entry_fx_rate=Decimal("1.0"), opened_at=_AT)
    ledger.close(f1.id, exit_fx_rate=Decimal("2.0"), closed_at=_AT)
    # PnL = 1000 × (2/1 - 1) = +1000
    f2 = ledger.open(_proposal(notional="1000"), entry_fx_rate=Decimal("1.0"), opened_at=_AT)
    ledger.close(f2.id, exit_fx_rate=Decimal("0.7"), closed_at=_AT)
    # PnL = 1000 × (0.7/1 - 1) = -300
    gross = ledger.realized_pnl_gross()
    assert gross.amount == Decimal("700.0")
    # After tax: 700 × 0.70 = 490
    after_tax = ledger.realized_pnl_after_tax()
    assert after_tax.amount == Decimal("490.00")


def test_net_negative_ledger_passes_through_pre_tax() -> None:
    """Losses are NOT taxed — they pass through pre-tax
    (REQ_F_FXH_006). The ledger returns gross == after_tax in this
    case."""
    ledger = FXHedgeLedger()
    f = ledger.open(_proposal(notional="1000"), entry_fx_rate=Decimal("1.0"), opened_at=_AT)
    ledger.close(f.id, exit_fx_rate=Decimal("0.5"), closed_at=_AT)
    gross = ledger.realized_pnl_gross()
    assert gross.amount == Decimal("-500.0")  # -50% on 1000
    after_tax = ledger.realized_pnl_after_tax()
    assert after_tax == gross


def test_empty_ledger_returns_zero_pnl() -> None:
    ledger = FXHedgeLedger()
    gross = ledger.realized_pnl_gross()
    assert gross == Money(Decimal(0), Currency.EUR)
    after_tax = ledger.realized_pnl_after_tax()
    assert after_tax == Money(Decimal(0), Currency.EUR)


def test_open_only_ledger_has_zero_realized_pnl() -> None:
    """Unclosed forwards contribute nothing to realised P&L
    (REQ_F_FXH_005 — mark is realised only at close)."""
    ledger = FXHedgeLedger()
    ledger.open(_proposal(), entry_fx_rate=Decimal("1.10"), opened_at=_AT)
    assert ledger.realized_pnl_gross().amount == Decimal(0)
