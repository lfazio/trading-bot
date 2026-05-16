"""Tests for the CR-006 cross-account-concentration gate wired into
``RiskEngine.pre_trade`` as gate 7 (REQ_F_ACC_008 / REQ_SDS_ACC_004).

The single-account default path SHALL be unaffected — passing
``cross_account_gate=None`` (the constructor default) makes gate 7
a no-op (REQ_NF_ACC_001 backwards compatibility).
"""

from __future__ import annotations

from trading_system.models.phase import MarketRegime
from trading_system.result import Err, Ok, Result
from tests.risk.test_engine import (
    StubPortfolio,
    make_engine,
    make_phase_constraints,
    make_proposal,
)


# ---------------------------------------------------------------------------
# Single-account / default — gate is a no-op
# ---------------------------------------------------------------------------


def test_gate_not_passed_acts_as_no_op() -> None:
    """Passing no ``cross_account_gate`` SHALL accept any proposal
    that passes gates 1-6 (REQ_NF_ACC_001)."""
    engine = make_engine()
    result = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
    )
    assert result.passed, f"expected accept, got reject({result.reasons!r})"


# ---------------------------------------------------------------------------
# Multi-account — gate runs AFTER gates 1-6
# ---------------------------------------------------------------------------


def test_gate_invoked_on_accepted_proposal() -> None:
    engine = make_engine()
    seen: list[object] = []

    def gate(p: object) -> Result[None, str]:
        seen.append(p)
        return Ok(None)

    result = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        cross_account_gate=gate,
    )
    assert result.passed
    assert len(seen) == 1


def test_gate_rejection_returns_categorised_reason() -> None:
    engine = make_engine()

    def gate(_p: object) -> Result[None, str]:
        return Err("risk:cross_account_concentration:ABC.AS")

    result = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        cross_account_gate=gate,
    )
    assert not result.passed
    assert result.reasons == ("risk:cross_account_concentration:ABC.AS",)


def test_gate_not_invoked_when_earlier_gate_rejects() -> None:
    """REQ_SDS_ACC_004 — cross-account gate runs AFTER per-account
    risk gates so the cheaper checks short-circuit. A proposal
    rejected by gate 2 (risk_per_trade_out_of_band) SHALL NOT reach
    the cross-account closure."""
    engine = make_engine()
    invoked = False

    def gate(_p: object) -> Result[None, str]:
        nonlocal invoked
        invoked = True
        return Ok(None)

    # size_pct_of_capital = 0.005 violates the default band [0.01, 0.02].
    result = engine.pre_trade(
        make_proposal(size="0.005"),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        cross_account_gate=gate,
    )
    assert not result.passed
    assert result.reasons == ("risk_per_trade_out_of_band",)
    assert invoked is False


def test_gate_determinism() -> None:
    """Two pre_trade calls with the same inputs + same gate closure
    SHALL produce identical results (REQ_NF_DET_001 family)."""
    engine = make_engine()

    def gate(_p: object) -> Result[None, str]:
        return Err("risk:cross_account_concentration:ABC.AS")

    a = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        cross_account_gate=gate,
    )
    b = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        cross_account_gate=gate,
    )
    assert a.passed == b.passed
    assert a.reasons == b.reasons
