"""Structural / contract tests for ``trading_system.accounts``.

Covers the read-only invariant on PortfolioGroup
(REQ_F_ACC_007 / REQ_SDD_ACC_004) at the package level + verifies
the public surface matches the documented exports.

REQ refs: REQ_F_ACC_007, REQ_NF_ACC_001, REQ_SDS_ACC_001..004,
REQ_SDD_ACC_008.
"""

from __future__ import annotations

import ast
from pathlib import Path

import trading_system.accounts as accounts_pkg


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ACCOUNTS_DIR = _REPO_ROOT / "trading_system" / "accounts"


def test_package_exports_documented_public_surface() -> None:
    """REQ_F_ACC_001..010 — every public class / function the design
    cascade documented SHALL be exported."""
    expected = {
        "Account",
        "AccountComponents",
        "AccountPipeline",
        "AccountRegistry",
        "AccountScopedTokenVerifier",
        "AccountSpec",
        "FranceCTOTaxModel",
        "HouseholdDrawdownTrigger",
        "PortfolioGroup",
        "TaxModel",
        "build_default_registry",
        "cross_account_concentration_gate",
        "load_accounts_yaml",
    }
    assert set(accounts_pkg.__all__) == expected
    for name in expected:
        assert hasattr(accounts_pkg, name), (
            f"trading_system.accounts is missing the public export {name!r}"
        )


def test_no_account_module_reaches_existing_concrete_portfolio() -> None:
    """REQ_F_ACC_007 / REQ_SDD_ACC_004 — the package SHALL NOT import
    ``trading_system.portfolio.portfolio`` (the concrete Portfolio
    type). The aggregator consumes the read-only Protocol-like
    accessors only so legacy callers can plug in any portfolio
    shape."""
    for py_file in _ACCOUNTS_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("trading_system.portfolio.portfolio"), (
                    f"{py_file.name} imports the concrete Portfolio module"
                )


def test_phase_6_foundation_does_not_touch_execution_layer() -> None:
    """The Phase-6 foundation slice is additive — no module SHALL
    import ``trading_system.execution`` because the runtime wiring
    follow-up will do that integration once."""
    for py_file in _ACCOUNTS_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("trading_system.execution"), (
                    f"{py_file.name} imports trading_system.execution — "
                    "Phase-6 foundation slice should stay additive"
                )


def test_main_py_builds_account_registry() -> None:
    """REQ_F_ACC_002 / REQ_F_ACC_003 — CR-006 Phase B replaces the
    pre-Phase-B "main.py SHALL NOT import accounts/" invariant
    with "main.py SHALL build the AccountRegistry via the factory".
    The legacy single-account default per REQ_NF_ACC_001 is the
    runtime's first registry consumer; multi-account deployments
    layer on via ``accounts.yaml`` (Phase-B follow-up)."""
    main_py = _REPO_ROOT / "trading_system" / "main.py"
    text = main_py.read_text(encoding="utf-8")
    # Phase B contract: main.py SHALL invoke the factory entry.
    assert "build_default_registry" in text, (
        "main.py does not call build_default_registry — Phase B wiring "
        "missing per REQ_F_ACC_002 / REQ_F_ACC_003"
    )
    # The household-drawdown observer SHALL be wired so REQ_F_ACC_009
    # actually fires on the demo path (no-op for under-threshold).
    assert "HouseholdDrawdownTrigger" in text, (
        "main.py does not import HouseholdDrawdownTrigger — REQ_F_ACC_009 "
        "wiring missing"
    )
