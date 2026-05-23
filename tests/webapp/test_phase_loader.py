"""Tests for the wizard's phase-from-capital helper."""

from __future__ import annotations

from decimal import Decimal

from trading_system.models.phase import AllocationBucket
from trading_system.webapp.runtimes.phase_loader import (
    phase_constraints_for_capital,
)


def test_phase_one_for_small_capital() -> None:
    """€1 000 SHALL land in Phase 1 (Capital Builder, up to 3 000 €)."""
    c = phase_constraints_for_capital(Decimal("1000"))
    assert c.max_positions == 3
    assert c.max_trades_per_month == 4
    # Phase-1 allocation: 90% STOCK, 10% TACTICAL, no turbo.
    assert c.allocation_targets.get(AllocationBucket.STOCK) == Decimal("0.90")
    assert c.turbo_exposure_max == Decimal("0")


def test_phase_two_for_capital_above_3k() -> None:
    """€5 000 SHALL land in Phase 2 (Stability, 3 000 - 10 000 €)."""
    c = phase_constraints_for_capital(Decimal("5000"))
    assert c.max_positions == 6
    assert c.max_trades_per_month == 8


def test_phase_three_for_capital_above_10k() -> None:
    """€25 000 SHALL land in Phase 3 (Systematic, 10 000 - 50 000 €)."""
    c = phase_constraints_for_capital(Decimal("25000"))
    assert c.max_positions == 12
    assert c.max_trades_per_month == 20


def test_phase_four_for_capital_above_50k() -> None:
    c = phase_constraints_for_capital(Decimal("100000"))
    assert c.max_positions >= 20


def test_phase_five_for_capital_above_200k() -> None:
    c = phase_constraints_for_capital(Decimal("500000"))
    assert c.max_positions >= 30
    # Phase-5 SHALL have a portfolio-vol cap per REQ_F_CAP_012.
    assert c.portfolio_vol_cap is not None


def test_phase_six_for_capital_above_1m() -> None:
    c = phase_constraints_for_capital(Decimal("5000000"))
    assert c.max_positions >= 50
    assert c.portfolio_vol_cap is not None


def test_phase_loader_falls_back_to_phase_one_on_bad_config_dir() -> None:
    """A missing / unreadable config dir SHALL return the
    Phase-1 fallback so a misconfigured deploy doesn't fail
    onboarding."""
    c = phase_constraints_for_capital(
        Decimal("10000"), config_dir="/nonexistent/path"
    )
    assert c.max_positions == 3
