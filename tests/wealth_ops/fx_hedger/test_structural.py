"""Structural / contract-level tests for ``wealth_ops/fx_hedger``.

Covers REQ_F_FXH_001 (module exists + ships the documented public
surface), REQ_SDS_FXH_001 (separate ledger; NO ``InstrumentClass.FX``
extension; existing consumers unchanged), and REQ_SDS_FXH_002 (pure
exposure + proposals + mark; ledger is the single mutable element).

REQ refs: REQ_F_FXH_001, REQ_SDS_FXH_001, REQ_SDS_FXH_002.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import trading_system.wealth_ops.fx_hedger as fx_hedger_pkg
from trading_system.models.instrument import InstrumentClass


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_FX_HEDGER_DIR = (
    _REPO_ROOT / "trading_system" / "wealth_ops" / "fx_hedger"
)


# ---------------------------------------------------------------------------
# REQ_F_FXH_001 — the package ships the documented public surface
# ---------------------------------------------------------------------------


def test_package_exports_documented_surface() -> None:
    """The ``__all__`` exports SHALL match the public surface
    documented in §3.30 + REQ_F_FXH_001."""
    expected = {
        "FXForward",
        "FXForwardState",
        "FXHedgeLedger",
        "FXHedger",
        "ForwardId",
        "HedgePolicy",
        "HedgeProposal",
        "MarkedPosition",
        "compute_fx_exposure",
        "mark",
    }
    assert set(fx_hedger_pkg.__all__) == expected
    for name in expected:
        assert hasattr(fx_hedger_pkg, name), f"missing public export: {name}"


# ---------------------------------------------------------------------------
# REQ_SDS_FXH_001 — separate ledger; no InstrumentClass.FX
# ---------------------------------------------------------------------------


def test_instrument_class_does_not_gain_fx_value() -> None:
    """REQ_SDS_FXH_001 — CR-011 SHALL NOT extend ``InstrumentClass``.
    The FX-hedge ledger is separate; existing consumers (risk
    class-cap, screener filters, structured-products gate) stay
    unchanged."""
    values = {member.value for member in InstrumentClass}
    assert "fx" not in values
    assert "FX" not in {member.name for member in InstrumentClass}


def test_fx_hedger_does_not_touch_existing_modules() -> None:
    """REQ_SDS_FXH_001 — the FX-hedger SHALL NOT import the runtime
    modules that would gain a new instrument-class case (risk,
    screener, structured_products). The hedger is a pure
    algorithmic core; integration into those modules is a
    Phase-6 follow-up."""
    forbidden = {
        "trading_system.risk",
        "trading_system.screener",
        "trading_system.structured_products",
    }
    for py_file in _FX_HEDGER_DIR.glob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prefix in forbidden:
                        assert not alias.name.startswith(prefix), (
                            f"{py_file.name} imports {alias.name} — "
                            "violates REQ_SDS_FXH_001"
                        )
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                for prefix in forbidden:
                    assert not module_name.startswith(prefix), (
                        f"{py_file.name} imports from {module_name} — "
                        "violates REQ_SDS_FXH_001"
                    )


# ---------------------------------------------------------------------------
# REQ_SDS_FXH_002 — pure functions + single mutable cursor
# ---------------------------------------------------------------------------


def test_ledger_is_only_mutable_element() -> None:
    """REQ_SDS_FXH_002 — the ledger holds the single mutable cursor.
    Forward, HedgeProposal, MarkedPosition, HedgePolicy SHALL be
    frozen dataclasses (verified via ``__setattr__`` raising)."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from trading_system.models.money import Currency, Money
    from trading_system.wealth_ops.fx_hedger import (
        FXForward,
        FXForwardState,
        ForwardId,
        HedgePolicy,
        HedgeProposal,
        MarkedPosition,
    )

    at = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)
    eur = Currency.EUR

    instances = [
        HedgePolicy(),
        MarkedPosition(
            currency=Currency.USD,
            value_in_base=Money(Decimal("30"), eur),
        ),
        HedgeProposal(
            currency=Currency.USD,
            base_currency=eur,
            exposure_amount=Money(Decimal("20000"), eur),
            target_hedge_ratio=Decimal("0.80"),
            decided_at=at,
        ),
        FXForward(
            id=ForwardId("fwd-1"),
            currency=Currency.USD,
            base_currency=eur,
            notional=Money(Decimal("16000"), eur),
            entry_fx_rate=Decimal("1.10"),
            opened_at=at,
            state=FXForwardState.OPEN,
        ),
    ]
    for obj in instances:
        with pytest.raises(Exception):
            # Frozen dataclasses raise FrozenInstanceError on
            # attribute assignment.
            object.__setattr__(obj, "currency", "WHATEVER")
            obj.currency = "WHATEVER"  # type: ignore[attr-defined]
