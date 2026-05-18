"""Tests for the CR-001 Phase B step 2 approval-gate wired into
``RiskEngine.pre_trade`` between the per-account gates (1-6) and
the cross-account gate (8) per REQ_SDS_NOT_002.

The single-account default path SHALL be unaffected — passing
``approval_gate=None`` (the constructor default) makes the gate
a no-op (REQ_NF_ACC_001 / REQ_NF_NOT_001 backwards compat).
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
# Default — no approval_gate ⇒ no-op
# ---------------------------------------------------------------------------


def test_approval_gate_not_passed_acts_as_no_op() -> None:
    """REQ_NF_NOT_001 — backtest + single-account demo passes
    approval_gate=None and the gate is bypassed."""
    engine = make_engine()
    result = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
    )
    assert result.passed, f"expected accept, got reject({result.reasons!r})"


# ---------------------------------------------------------------------------
# Gate invocation + Err propagation
# ---------------------------------------------------------------------------


def test_approval_gate_invoked_on_accepted_proposal() -> None:
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
        approval_gate=gate,
    )
    assert result.passed
    assert len(seen) == 1


def test_approval_gate_rejection_returns_categorised_reason() -> None:
    engine = make_engine()

    def gate(_p: object) -> Result[None, str]:
        return Err("notifications:approval_timeout:req-123")

    result = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        approval_gate=gate,
    )
    assert not result.passed
    assert result.reasons == ("notifications:approval_timeout:req-123",)


def test_approval_gate_default_deny_on_timeout() -> None:
    """REQ_F_NOT_004 — default-deny on timeout. The gate returns
    ``Err("notifications:approval_timeout:<id>")`` which propagates
    through the rejection."""
    engine = make_engine()

    def gate(_p: object) -> Result[None, str]:
        return Err("notifications:approval_timeout:req-x")

    result = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        approval_gate=gate,
    )
    assert not result.passed
    assert result.reasons[0].startswith("notifications:approval_timeout:")


# ---------------------------------------------------------------------------
# Ordering: approval gate runs BEFORE cross-account gate (REQ_SDS_NOT_002)
# ---------------------------------------------------------------------------


def test_approval_gate_runs_before_cross_account_gate() -> None:
    """REQ_SDS_NOT_002 — evaluation order:
    per-account risk → approval → cross-account risk → submit.
    When the approval gate rejects, the cross-account gate SHALL
    NOT be invoked."""
    engine = make_engine()
    cross_calls: list[object] = []

    def approval(_p: object) -> Result[None, str]:
        return Err("notifications:approval_denied:req-x")

    def cross(p: object) -> Result[None, str]:
        cross_calls.append(p)
        return Ok(None)

    result = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        approval_gate=approval,
        cross_account_gate=cross,
    )
    assert not result.passed
    assert "approval_denied" in result.reasons[0]
    assert cross_calls == [], "cross-account gate fired despite approval reject"


def test_approval_gate_passes_through_to_cross_account_gate_on_ok() -> None:
    engine = make_engine()
    approval_calls: list[object] = []
    cross_calls: list[object] = []

    def approval(p: object) -> Result[None, str]:
        approval_calls.append(p)
        return Ok(None)

    def cross(p: object) -> Result[None, str]:
        cross_calls.append(p)
        return Ok(None)

    result = engine.pre_trade(
        make_proposal(),
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        approval_gate=approval,
        cross_account_gate=cross,
    )
    assert result.passed
    assert len(approval_calls) == 1
    assert len(cross_calls) == 1


def test_approval_gate_not_invoked_when_earlier_gate_rejects() -> None:
    """Per-account gates 1-6 short-circuit. The approval gate is
    NOT invoked when an earlier gate rejects."""
    engine = make_engine()
    gate_calls: list[object] = []

    def approval(p: object) -> Result[None, str]:
        gate_calls.append(p)
        return Ok(None)

    # Force gate 2 (risk-per-trade-band) to reject by passing a
    # proposal whose size is outside the default 0.01..0.02 band.
    proposal = make_proposal(size="0.99")
    result = engine.pre_trade(
        proposal,
        StubPortfolio(),
        make_phase_constraints(),
        MarketRegime.BULL,
        approval_gate=approval,
    )
    assert not result.passed
    assert gate_calls == [], "approval gate fired despite earlier reject"
