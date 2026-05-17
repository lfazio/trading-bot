"""Structural tests for ``trading_system.strategy_lab.quant``.

Covers REQ_NF_QNT_001 (offline-only — no runtime module SHALL import
``strategy_lab.quant``) and REQ_SDS_QNT_001 (the package exports
the documented public surface)."""

from __future__ import annotations

import ast
from pathlib import Path

import trading_system.strategy_lab.quant as quant_pkg


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_QUANT_DIR = _REPO_ROOT / "trading_system" / "strategy_lab" / "quant"
_TRADING_SYSTEM_DIR = _REPO_ROOT / "trading_system"
_STRATEGY_LAB_DIR = _TRADING_SYSTEM_DIR / "strategy_lab"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_package_exports_documented_public_surface() -> None:
    expected = {
        "DEFAULT_METRIC_VOCABULARY",
        "BacktesterAdapter",
        "DatasetWindow",
        "Direction",
        "EvaluatorAdapter",
        "Hypothesis",
        "HypothesisId",
        "HypothesisLibrary",
        "HypothesisResult",
        "HypothesisRunner",
        "HypothesisState",
        "HypothesisValidator",
        "InMemoryHypothesisStore",
        "OverfittingConfig",
        "QuantConfig",
        "ValidatorConfig",
        "adjusted_sharpe",
        "information_coefficient",
        "load_quant_config",
        "overfitting_gate",
        "parameter_to_data_ratio",
    }
    assert set(quant_pkg.__all__) == expected
    for name in expected:
        assert hasattr(quant_pkg, name), (
            f"trading_system.strategy_lab.quant is missing export {name!r}"
        )


# ---------------------------------------------------------------------------
# Offline-only invariant (REQ_NF_QNT_001)
# ---------------------------------------------------------------------------


def test_no_runtime_module_imports_strategy_lab_quant() -> None:
    """No runtime decisioning module SHALL import
    ``trading_system.strategy_lab.quant`` (REQ_NF_QNT_001). The
    offline-only invariant keeps the meta-loop research code off
    the trading critical path.

    Documented exceptions:
      - ``trading_system/config/validator.py`` imports
        ``strategy_lab.quant.loader.load_quant_config`` so the C2
        startup gate can validate ``config/quant.yaml`` against the
        typed schema. This is config-validation only — the loader
        returns a frozen ``QuantConfig`` and never instantiates a
        HypothesisValidator / HypothesisLibrary. The trading hot
        path stays offline-quant-blind.
    """
    allowed_callers = (_TRADING_SYSTEM_DIR / "config" / "validator.py",)
    offenders: list[str] = []
    for py_file in _TRADING_SYSTEM_DIR.rglob("*.py"):
        # Skip the package itself + every strategy_lab module.
        try:
            py_file.relative_to(_STRATEGY_LAB_DIR)
            continue
        except ValueError:
            pass
        if py_file in allowed_callers:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("trading_system.strategy_lab.quant"):
                    offenders.append(str(py_file))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("trading_system.strategy_lab.quant"):
                        offenders.append(str(py_file))
    assert offenders == [], (
        "REQ_NF_QNT_001 — these runtime modules import "
        f"strategy_lab.quant: {offenders}"
    )


def test_quant_package_only_imports_allowed_dependencies() -> None:
    """Defence in depth: catalogue what the quant package itself
    imports from trading_system. Allowed: ``models``, ``result``,
    ``strategy_lab.metrics``. Forbidden: every other top-level
    package (``risk``, ``execution``, ``safety``, ``backtesting``,
    ``accounts``, ``observability``, ``config``, ``persistence``).
    The runner's adapters Protocol means callers wire concrete
    types in from outside — the quant package itself never reaches
    them."""
    forbidden_prefixes = (
        "trading_system.risk",
        "trading_system.execution",
        "trading_system.safety",
        "trading_system.backtesting",
        "trading_system.accounts",
        "trading_system.observability",
        "trading_system.config",
        "trading_system.persistence",
        "trading_system.dashboard",
        "trading_system.analytics",
        "trading_system.portfolio",
        "trading_system.portfolio_manager",
        "trading_system.tax",
        "trading_system.regime",
        "trading_system.turbo_selector",
        "trading_system.screener",
        "trading_system.phase_engine",
        "trading_system.capital_flow",
        "trading_system.milestone_controller",
        "trading_system.structured_products",
        "trading_system.wealth_ops",
        "trading_system.data",
    )
    for py_file in _QUANT_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for prefix in forbidden_prefixes:
                    assert not module.startswith(prefix), (
                        f"{py_file.name} imports {module} — "
                        f"strategy_lab.quant must not depend on {prefix}"
                    )
