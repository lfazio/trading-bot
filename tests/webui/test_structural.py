"""Structural test for ``trading_system.webui``.

REQ_F_WEB_007 — the webui SHALL NOT reach ``execution`` /
``BrokerAdapter`` / any concrete broker adapter. Mirrors the
REQ_F_PER_010 audit for the persistence layer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import trading_system.webui as webui_pkg


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WEBUI_DIR = _REPO_ROOT / "trading_system" / "webui"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_package_exports_documented_public_surface() -> None:
    expected = {
        "IdempotencyStore",
        "InMemoryIdempotencyStore",
        "JsonResponse",
        "LiveStateResponse",
        "PromoteResponse",
        "Request",
        "Route",
        "Router",
        "WebAuth",
        "WebUIServer",
        "canonical_response",
    }
    assert set(webui_pkg.__all__) == expected
    for name in expected:
        assert hasattr(webui_pkg, name), (
            f"trading_system.webui is missing export {name!r}"
        )


# ---------------------------------------------------------------------------
# Import-graph audit (REQ_F_WEB_007 / REQ_NF_WEB_001)
# ---------------------------------------------------------------------------


def test_webui_does_not_import_execution_or_broker_adapter() -> None:
    """A test SHALL walk every ``.py`` file under
    ``trading_system/webui/`` and assert that none of them import
    from ``trading_system.execution``, ``BrokerAdapter``, or any
    concrete broker adapter class (REQ_F_WEB_007)."""
    forbidden_prefixes = (
        "trading_system.execution",
        # The mock data provider is allowed for tests but the webui
        # SHALL NOT reach it; runtime data flows through the existing
        # MarketDataProvider Protocol surface only.
        "trading_system.data.mock",
    )
    forbidden_symbols = (
        "BrokerAdapter",
        "LocalBrokerAdapter",
    )
    for py_file in _WEBUI_DIR.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for prefix in forbidden_prefixes:
                    assert not module.startswith(prefix), (
                        f"{py_file.name} imports {module} — "
                        f"webui must not depend on {prefix}"
                    )
                for sym in forbidden_symbols:
                    assert all(alias.name != sym for alias in node.names), (
                        f"{py_file.name} imports {sym} from {module} — "
                        "REQ_F_WEB_007 import-graph audit"
                    )


def test_webui_does_not_reach_backtesting_or_strategy_lab_directly() -> None:
    """Phase A also keeps the webui out of ``backtesting`` and
    ``strategy_lab`` — Phase B will wire those via the
    ``JobQueue`` / ``BacktesterAdapter`` Protocols, not direct
    imports."""
    forbidden_prefixes = (
        "trading_system.backtesting",
        "trading_system.strategy_lab",
    )
    for py_file in _WEBUI_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for prefix in forbidden_prefixes:
                    assert not module.startswith(prefix), (
                        f"{py_file.name} imports {module} — "
                        f"webui Phase A must not depend on {prefix}"
                    )


# ---------------------------------------------------------------------------
# REQ_SDD_WEB_006 — routes-specific tightened audit
# ---------------------------------------------------------------------------


def test_routes_specifically_import_only_protocols_and_schemas() -> None:
    """REQ_SDD_WEB_006 — every ``webui/routes/*.py`` SHALL go
    through Protocol-shaped readers + ``webui.schemas`` /
    ``webui.auth`` / ``webui.server`` only. Concrete
    runtime types (Portfolio, Analytics, Registry, RiskEngine,
    BrokerAdapter) SHALL be reached via the Protocol slot on
    ``app.state``, never imported directly. This is a tighter
    audit than the package-wide one above so route files stay
    plumbing-only.
    """
    routes_dir = _WEBUI_DIR / "routes"
    # Routes may freely import from these prefixes — everything
    # else (concrete runtime types) is reached through a
    # Protocol parameter the handler receives at construction.
    allowed_project_prefixes = (
        "trading_system.webui",
        "trading_system.models",
        "trading_system.accounts",
        "trading_system.notifications",
        "trading_system.persistence",  # Protocol slot for repos
        "trading_system.result",
    )
    forbidden_concrete_modules = (
        "trading_system.execution",
        "trading_system.safety",
        "trading_system.risk",
        "trading_system.strategy_lab",
        "trading_system.backtesting",
        "trading_system.data",
        "trading_system.portfolio",
        "trading_system.analytics",
        "trading_system.dashboard",
    )
    for py_file in routes_dir.rglob("*.py"):
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
                for forbidden in forbidden_concrete_modules:
                    assert not module.startswith(forbidden), (
                        f"{py_file.name} imports {module} — "
                        f"REQ_SDD_WEB_006 routes audit: routes SHALL NOT "
                        f"reach {forbidden} directly; use a Protocol slot"
                    )
                assert any(
                    module.startswith(p) for p in allowed_project_prefixes
                ), (
                    f"{py_file.name} imports {module} — "
                    "not in the closed routes allow-list "
                    "(REQ_SDD_WEB_006)"
                )
