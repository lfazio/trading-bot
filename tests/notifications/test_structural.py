"""Structural test for ``trading_system.notifications``.

Verifies the public surface matches the documented exports and
pins the import-graph audit (the package SHALL be reachable from
``safety/`` + ``main.py`` + future ``webui/`` but SHALL NOT pull
``execution`` / ``backtesting`` / ``strategy_lab`` into the trading
critical path).
"""

from __future__ import annotations

import ast
from pathlib import Path

import trading_system.notifications as nots_pkg


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_NOT_DIR = _REPO_ROOT / "trading_system" / "notifications"


def test_package_exports_documented_public_surface() -> None:
    expected = {
        "AlertChannel",
        "AnalyticsReader",
        "AnomalyAlert",
        "ApprovalConfig",
        "ApprovalGate",
        "ApprovalResponse",
        "Error",
        "KillSwitchEvent",
        "LocalLogChannel",
        "MemoryNotificationChannel",
        "NotificationChannel",
        "NotificationFanOut",
        "NotificationPayload",
        "NotificationsConfig",
        "PortfolioReader",
        "RealizationLine",
        "RegistryReader",
        "ResponseInbox",
        "RetryConfig",
        "RetryPolicy",
        "Summary",
        "SummaryPublisher",
        "TradeApprovalRequest",
        "canonical_json_line",
        "load_notifications_config",
    }
    assert set(nots_pkg.__all__) == expected
    for name in expected:
        assert hasattr(nots_pkg, name), (
            f"trading_system.notifications is missing export {name!r}"
        )


def test_notifications_does_not_reach_execution_or_backtesting() -> None:
    """REQ_NF_NOT_001 — the notifications package SHALL NOT pull
    ``trading_system.execution`` / ``backtesting`` / ``strategy_lab``
    into its import graph so the trade-execution critical path stays
    off the fan-out (approval gate is the only synchronous
    exception, handled by ``approval.py`` via the gate's
    ``evaluate`` API which the caller decides whether to fire)."""
    forbidden_prefixes = (
        "trading_system.execution",
        "trading_system.backtesting",
        "trading_system.strategy_lab",
        "trading_system.risk",
        "trading_system.dashboard",
        "trading_system.webui",
    )
    for py_file in _NOT_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for prefix in forbidden_prefixes:
                    assert not module.startswith(prefix), (
                        f"{py_file.name} imports {module} — "
                        f"notifications must not depend on {prefix}"
                    )
