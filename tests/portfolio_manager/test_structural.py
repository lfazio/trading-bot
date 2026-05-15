"""Structural / contract-level tests for ``portfolio_manager``.

Covers TC_PMG_009 (read-only invariant — no Portfolio mutators
reached) + TC_PMG_010 (deferred-wiring smoke; main.py keeps working
without the package in the trade-decision pipeline).

REQ refs: REQ_F_PMG_007, REQ_F_PMG_008, REQ_SDS_PMG_001.
"""

from __future__ import annotations

import ast
from pathlib import Path

import trading_system.portfolio_manager as pm_pkg


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PM_DIR = _REPO_ROOT / "trading_system" / "portfolio_manager"
_MAIN_PATH = _REPO_ROOT / "trading_system" / "main.py"

# The closed set of Portfolio mutator names from the SDD. A mutating
# call from portfolio_manager/ is a violation of REQ_F_PMG_007 (the
# package is read-only with respect to Portfolio).
_FORBIDDEN_MUTATORS: frozenset[str] = frozenset(
    {
        "apply_trade",
        "apply_dividend",
        "record_equity",
        "record_realization",
    }
)


# ---------------------------------------------------------------------------
# TC_PMG_009 — read-only audit
# ---------------------------------------------------------------------------


def test_no_portfolio_mutator_calls_in_portfolio_manager() -> None:
    """REQ_F_PMG_007 / REQ_SDS_PMG_001 — AST walk every .py file
    under portfolio_manager/ and assert no attribute access matches
    a Portfolio mutator name. The package consumes immutable views;
    mutating calls go through the existing trade-execution path."""
    offenders: list[tuple[Path, str]] = []
    for py_file in _PM_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_MUTATORS:
                offenders.append((py_file.relative_to(_REPO_ROOT), node.attr))
    assert offenders == [], (
        f"portfolio_manager/ reaches Portfolio mutators: {offenders} — "
        "violates REQ_F_PMG_007"
    )


def test_portfolio_manager_does_not_import_existing_mutating_modules() -> None:
    """The package SHALL NOT import the execution layer or the
    portfolio module's mutating types — proposal generators consume
    immutable view shapes only."""
    forbidden_modules = {
        "trading_system.execution",
        "trading_system.portfolio.portfolio",
    }
    for py_file in _PM_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for forbidden in forbidden_modules:
                    assert not module.startswith(forbidden), (
                        f"{py_file.name} imports from {module} — "
                        "portfolio_manager/ stays read-only over Portfolio"
                    )


def test_package_exports_documented_surface() -> None:
    """REQ_F_PMG_001 — the package ships the documented public
    surface."""
    expected = {
        "AttributionDecomposition",
        "Cadence",
        "HarvestablePosition",
        "RebalanceDirection",
        "RebalanceProposal",
        "Rebalancer",
        "SectorRotatorFacade",
        "TaxHarvesterFacade",
        "attribution_decomposition",
    }
    assert set(pm_pkg.__all__) == expected
    for name in expected:
        assert hasattr(pm_pkg, name)


# ---------------------------------------------------------------------------
# TC_PMG_010 — deferred-wiring smoke
# ---------------------------------------------------------------------------


def test_main_py_does_not_import_portfolio_manager() -> None:
    """REQ_F_PMG_008 — the Phase-5 deliverable ships the algorithmic
    core; main.py keeps working without the package in the
    trade-decision pipeline. The Phase-6 follow-up rewires
    strategies → portfolio_manager → risk in main.py."""
    text = _MAIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(_MAIN_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("trading_system.portfolio_manager"), (
                "main.py imports portfolio_manager — this CR's Phase-5 "
                "scope explicitly defers that wiring to Phase 6"
            )
