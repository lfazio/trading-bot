"""Structural audits for the CR-019 view-router expansion.

REQ refs:
- REQ_SDS_WEB2_001 — every view router SHALL live under
  ``trading_system/webapp/routers/views/``; api routers SHALL
  live under ``trading_system/webapp/routers/api/``.
- REQ_SDS_WEB2_003 — the CR-019 accessibility surface SHALL be
  auditable from the test suite (contrast, focus-trap, aria-label,
  motion preferences).

This file is structural only — no FastAPI rendering. It walks
the filesystem + the routers.* exports and asserts the
documented layout.
"""

from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VIEW_DIR = _REPO_ROOT / "trading_system" / "webapp" / "routers" / "views"
_API_DIR = _REPO_ROOT / "trading_system" / "webapp" / "routers" / "api"
_TESTS_DIR = _REPO_ROOT / "tests" / "webapp"


def test_view_routers_live_under_routers_views_directory() -> None:
    """REQ_SDS_WEB2_001 — every router file that registers HTML
    view routes SHALL live under ``routers/views/`` (so the
    closed-import audit can scope the auth-tier rules to that
    directory alone)."""
    assert _VIEW_DIR.is_dir(), "expected routers/views/ directory"
    # At least the documented view routers exist.
    expected = {
        "dashboard.py",
        "jobs.py",
        "login.py",
        "notifications.py",
        "onboarding.py",
        "paper_session.py",
        "recovery.py",
        "reports.py",
    }
    present = {p.name for p in _VIEW_DIR.iterdir() if p.suffix == ".py"}
    missing = expected - present
    assert not missing, f"view routers missing: {missing}"


def test_api_routers_live_under_routers_api_directory() -> None:
    """REQ_SDS_WEB2_001 — every router that emits canonical-JSON
    SHALL live under ``routers/api/``. The split keeps the
    structural audit's "views may redirect to /login" vs "api
    SHALL 401 with categorised Err" rules cleanly scoped."""
    assert _API_DIR.is_dir(), "expected routers/api/ directory"
    expected = {
        "backtests.py",
        "inbox.py",
        "live_state.py",
        "paper_state.py",
        "registry.py",
        "session.py",
    }
    present = {p.name for p in _API_DIR.iterdir() if p.suffix == ".py"}
    missing = expected - present
    assert not missing, f"api routers missing: {missing}"


def test_no_router_files_at_routers_top_level() -> None:
    """REQ_SDS_WEB2_001 — router files SHALL live in views/ or
    api/ subdirectories, NOT directly under routers/."""
    routers_dir = _REPO_ROOT / "trading_system" / "webapp" / "routers"
    rogue = [
        p
        for p in routers_dir.iterdir()
        if p.is_file() and p.suffix == ".py" and p.name != "__init__.py"
    ]
    assert not rogue, (
        f"router files SHALL NOT live at routers/ top level; found: {rogue}"
    )


def test_view_routers_import_from_webapp_only() -> None:
    """REQ_SDS_WEB2_001 — view routers SHALL only consume
    Protocol-shaped slots from ``app.state``; they SHALL NOT
    reach for concrete decisioning types (``execution``, ``risk``,
    ``safety``, ``strategy_lab``). The carve-out for
    ``trading_system.webapp.runtimes.*`` stands so the wizard's
    finish handler can dispatch the strategy factory."""
    forbidden = (
        "trading_system.execution",
        "trading_system.risk",
        "trading_system.safety",
        "trading_system.strategy_lab",
    )
    for py_file in sorted(_VIEW_DIR.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                for prefix in forbidden:
                    assert not module.startswith(prefix), (
                        f"{py_file.relative_to(_REPO_ROOT)} imports "
                        f"{module} — view routers SHALL go through "
                        f"Protocol slots on app.state (REQ_SDS_WEB2_001)."
                    )


# ---------------------------------------------------------------------------
# REQ_SDS_WEB2_003 — accessibility surface IS auditable in tests/webapp/
# ---------------------------------------------------------------------------


def test_accessibility_audit_suite_exists() -> None:
    """REQ_SDS_WEB2_003 — the CR-019 accessibility surface (WCAG
    contrast, focus-trap, aria-labels, prefers-reduced-motion)
    SHALL be covered by tests under ``tests/webapp/``."""
    required = {
        "test_accessibility.py",          # REQ_NF_WEB2_002 contrast audit
        "test_aria_label_audit.py",       # REQ_NF_WEB2_005 + REQ_SDD_WEB2_009
        "test_a11y_motion_and_focus.py",  # REQ_NF_WEB2_004 + REQ_SDD_WEB2_007/008
    }
    present = {p.name for p in _TESTS_DIR.iterdir() if p.suffix == ".py"}
    missing = required - present
    assert not missing, (
        f"missing accessibility audit files: {missing} — REQ_SDS_WEB2_003"
    )


def test_accessibility_files_reference_their_anchor_reqs() -> None:
    """Pin the REQ-id references in each audit file so a future
    refactor can't quietly drop them — the traceability tool
    relies on these markers to lift the SRS / SDD REQs from TP."""
    pairs = {
        "test_accessibility.py": ("REQ_NF_WEB2_002", "REQ_SDD_WEB2_006"),
        "test_aria_label_audit.py": ("REQ_NF_WEB2_005", "REQ_SDD_WEB2_009"),
        "test_a11y_motion_and_focus.py": (
            "REQ_NF_WEB2_004",
            "REQ_SDD_WEB2_007",
            "REQ_SDD_WEB2_008",
        ),
    }
    for filename, expected_refs in pairs.items():
        body = (_TESTS_DIR / filename).read_text(encoding="utf-8")
        for ref in expected_refs:
            assert ref in body, f"{filename} SHALL reference {ref}"
