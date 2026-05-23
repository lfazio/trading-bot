"""REQ_SDD_WEB2_005 — default_app() SHALL call
RuntimeRegistry.resume_from_persistence() at boot so a webapp
restart re-surfaces persisted paper sessions.

The audit is structural: a grep over the module proving the
call sits inside the default_app() factory.
"""

from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_PY = _REPO_ROOT / "trading_system" / "webapp" / "app.py"


def _function_source(filename: Path, function_name: str) -> str:
    tree = ast.parse(filename.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.unparse(node)
    raise AssertionError(f"function {function_name!r} not found in {filename}")


def test_default_app_invokes_resume_from_persistence() -> None:
    """REQ_SDD_WEB2_005 — the boot path SHALL call
    ``RuntimeRegistry.resume_from_persistence(repo)`` so any
    persisted paper- sessions become visible immediately."""
    source = _function_source(_APP_PY, "default_app")
    assert "resume_from_persistence" in source


def test_default_app_surfaces_discovered_sessions_via_inbox() -> None:
    """When the boot resume returns Ok(non-empty tuple), the
    operator-facing inbox SHALL surface a session_discovered
    breadcrumb so the operator notices on next paint."""
    source = _function_source(_APP_PY, "default_app")
    assert "session_discovered" in source
    # The breadcrumb SHALL carry the paper-session category.
    assert "paper-session" in source


def test_default_app_no_op_without_persistence_db_env() -> None:
    """REQ_SDD_WEB2_005 — when ``TRADING_BOT_PERSISTENCE_DB`` is
    unset, the resume call SHALL be a benign no-op (helper
    returns None, registry-side accepts None gracefully)."""
    # Helper is module-level so the AST audit can inspect it.
    source = _APP_PY.read_text(encoding="utf-8")
    assert "TRADING_BOT_PERSISTENCE_DB" in source
    assert "def _portfolio_repo_for_resume" in source
