"""Tests for ``trading_system.wealth_ops.fx_hedger.forward``.

Covers TC_FXH_002 (FXForward invariants) + HedgeProposal invariants.

REQ refs: REQ_F_FXH_005, REQ_SDD_FXH_001, REQ_SDD_TYP_003.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.money import Currency, Money
from trading_system.wealth_ops.fx_hedger.forward import (
    FXForward,
    FXForwardState,
    ForwardId,
    HedgeProposal,
)


_AT = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)


def _proposal(**overrides: object) -> HedgeProposal:
    defaults: dict[str, object] = {
        "currency": Currency.USD,
        "base_currency": Currency.EUR,
        "exposure_amount": Money(Decimal("20000"), Currency.EUR),
        "target_hedge_ratio": Decimal("0.80"),
        "decided_at": _AT,
    }
    defaults.update(overrides)
    return HedgeProposal(**defaults)  # type: ignore[arg-type]


def _forward(**overrides: object) -> FXForward:
    defaults: dict[str, object] = {
        "id": ForwardId("fwd-1"),
        "currency": Currency.USD,
        "base_currency": Currency.EUR,
        "notional": Money(Decimal("16000"), Currency.EUR),
        "entry_fx_rate": Decimal("1.10"),
        "opened_at": _AT,
        "state": FXForwardState.OPEN,
    }
    defaults.update(overrides)
    return FXForward(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FXForwardState — StrEnum membership
# ---------------------------------------------------------------------------


def test_state_enum_values() -> None:
    assert set(FXForwardState) == {FXForwardState.OPEN, FXForwardState.CLOSED}
    assert FXForwardState.OPEN.value == "open"
    assert FXForwardState.CLOSED.value == "closed"


# ---------------------------------------------------------------------------
# HedgeProposal invariants
# ---------------------------------------------------------------------------


def test_hedge_proposal_rejects_same_currency_pair() -> None:
    with pytest.raises(ValueError, match="must differ from base_currency"):
        _proposal(currency=Currency.EUR)


def test_hedge_proposal_rejects_exposure_in_wrong_currency() -> None:
    with pytest.raises(ValueError, match="exposure_amount.currency must equal"):
        _proposal(exposure_amount=Money(Decimal("20000"), Currency.USD))


def test_hedge_proposal_rejects_ratio_outside_open_unit_interval() -> None:
    with pytest.raises(ValueError, match="target_hedge_ratio"):
        _proposal(target_hedge_ratio=Decimal("0"))
    with pytest.raises(ValueError, match="target_hedge_ratio"):
        _proposal(target_hedge_ratio=Decimal("1.01"))


def test_hedged_notional_is_exposure_times_ratio() -> None:
    proposal = _proposal(
        exposure_amount=Money(Decimal("20000"), Currency.EUR),
        target_hedge_ratio=Decimal("0.80"),
    )
    assert proposal.hedged_notional() == Money(Decimal("16000.00"), Currency.EUR)


# ---------------------------------------------------------------------------
# FXForward invariants
# ---------------------------------------------------------------------------


def test_forward_rejects_same_currency_pair() -> None:
    with pytest.raises(ValueError, match="must differ from base_currency"):
        _forward(currency=Currency.EUR)


def test_forward_rejects_notional_in_wrong_currency() -> None:
    with pytest.raises(ValueError, match="notional.currency must equal"):
        _forward(notional=Money(Decimal("16000"), Currency.USD))


def test_forward_rejects_non_positive_entry_rate() -> None:
    with pytest.raises(ValueError, match="entry_fx_rate"):
        _forward(entry_fx_rate=Decimal("0"))
    with pytest.raises(ValueError, match="entry_fx_rate"):
        _forward(entry_fx_rate=Decimal("-1.0"))


def test_closed_forward_requires_exit_fields() -> None:
    with pytest.raises(ValueError, match="exit_fx_rate"):
        _forward(state=FXForwardState.CLOSED)
    with pytest.raises(ValueError, match="closed_at"):
        _forward(
            state=FXForwardState.CLOSED,
            exit_fx_rate=Decimal("1.05"),
            closed_at=None,
        )


def test_closed_forward_rejects_non_positive_exit_rate() -> None:
    with pytest.raises(ValueError, match="exit_fx_rate"):
        _forward(
            state=FXForwardState.CLOSED,
            exit_fx_rate=Decimal("0"),
            closed_at=_AT,
        )


def test_open_forward_rejects_exit_fields() -> None:
    with pytest.raises(ValueError, match="OPEN FXForward must not"):
        _forward(
            state=FXForwardState.OPEN,
            exit_fx_rate=Decimal("1.05"),
            closed_at=_AT,
        )


def test_open_forward_happy_path() -> None:
    f = _forward()
    assert f.state is FXForwardState.OPEN
    assert f.exit_fx_rate is None
    assert f.closed_at is None


def test_closed_forward_happy_path() -> None:
    f = _forward(
        state=FXForwardState.CLOSED,
        exit_fx_rate=Decimal("1.05"),
        closed_at=_AT,
    )
    assert f.state is FXForwardState.CLOSED
    assert f.exit_fx_rate == Decimal("1.05")
    assert f.closed_at == _AT
