"""TC_MCS_009 — composition without engine modification.

REQ refs:
- REQ_SDS_MCS_001 — L5 placement + closed import-graph (the
  ``backtesting/monte_carlo/`` package sits at L5 between
  ``backtesting/engine.py`` and ``strategy_lab/``).
- REQ_SDD_MCS_001 — closed import-graph.
- REQ_SDD_MCS_002 — no engine fork; ``backtesting/monte_carlo/``
  SHALL NOT redefine ``Backtest``, ``EventClock``, ``MarketReplay``,
  ``InjectionScheduler``, ``DividendSimulator``, ``KnockoutSimulator``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "trading_system"
    / "backtesting"
    / "monte_carlo"
)

_FORBIDDEN_CLASS_NAMES = frozenset(
    {
        "Backtest",
        "EventClock",
        "MarketReplay",
        "InjectionScheduler",
        "DividendSimulator",
        "KnockoutSimulator",
    }
)

_ALLOWED_IMPORT_PREFIXES = (
    # Project-local modules per REQ_SDD_MCS_001.
    "trading_system.backtesting.engine",
    "trading_system.backtesting.result",
    "trading_system.backtesting.walk_forward",
    "trading_system.backtesting.monte_carlo",
    "trading_system.regime",
    "trading_system.data",
    "trading_system.result",
    "trading_system.models",  # Bar transitively via data.types
    # stdlib (recognised by absence of a "trading_system" prefix).
)

_FORBIDDEN_IMPORT_PREFIXES = (
    "trading_system.execution",
    "trading_system.risk",
    "trading_system.safety",
    "trading_system.strategy_lab",
    "trading_system.portfolio_manager",
    "trading_system.accounts",
    "trading_system.notifications",
    "trading_system.webui",
    "trading_system.webapp",
)


def _walk_py_files() -> list[Path]:
    return [p for p in _PACKAGE_DIR.rglob("*.py") if p.name != "__init__.py" or True]


def test_no_redefinition_of_engine_classes() -> None:
    """REQ_SDD_MCS_002 — grep for forbidden class names."""
    for py_file in _walk_py_files():
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name not in _FORBIDDEN_CLASS_NAMES, (
                    f"{py_file.name} redefines {node.name} — "
                    "REQ_SDD_MCS_002 forbids forking engine.py"
                )


def test_import_graph_closed() -> None:
    """REQ_SDD_MCS_001 — every project-local import SHALL match one of
    the allowed prefixes; nothing under
    ``execution`` / ``risk`` / ``safety`` / ``strategy_lab`` / … SHALL
    appear."""
    for py_file in _walk_py_files():
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if not module.startswith("trading_system."):
                    continue
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not module.startswith(forbidden), (
                        f"{py_file.name} imports {module} — "
                        f"REQ_SDD_MCS_001 forbids {forbidden}.*"
                    )
                # Allow-list check — every project-local import SHALL match.
                assert any(
                    module.startswith(p) for p in _ALLOWED_IMPORT_PREFIXES
                ), (
                    f"{py_file.name} imports {module} — not in the closed "
                    "allow-list (REQ_SDD_MCS_001)"
                )


def test_runner_routes_through_backtest_factory() -> None:
    """REQ_SDD_MCS_002 — the runner SHALL invoke the injected
    ``backtest_factory`` callable rather than constructing Backtest
    directly. Sanity-check: ``runner.py`` references
    ``self.backtest_factory(`` at least once and does NOT reference
    ``Backtest.assemble(`` or ``Backtest(``."""
    runner_src = (_PACKAGE_DIR / "runner.py").read_text(encoding="utf-8")
    assert "self.backtest_factory(" in runner_src
    assert "Backtest.assemble(" not in runner_src
    assert "Backtest(" not in runner_src
