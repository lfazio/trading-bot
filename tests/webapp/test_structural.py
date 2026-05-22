"""TC_FAS_010 + TC_FAS_011 — structural audits.

REQ refs:
- REQ_SDS_FAS_001 — L7 placement + closed import graph.
- REQ_SDS_FAS_003 — no Node toolchain; bundled HTMX assets only.
- REQ_SDD_FAS_001 — AST audit forbids ``execution`` / ``safety`` /
  ``risk`` / ``strategy_lab`` / ``data`` reach.
- REQ_SDD_FAS_003 — repo-wide grep rejects node_modules /
  package.json / vite.config / webpack.config / tsconfig.json.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PACKAGE_DIR = _REPO_ROOT / "trading_system" / "webapp"
_STATIC_DIR = _PACKAGE_DIR / "static"
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"


_ALLOWED_PROJECT_IMPORT_PREFIXES = (
    "trading_system.webapp",
    "trading_system.webui",  # canonical schemas (LiveStateResponse, PromoteResponse) shared
    "trading_system.models",
    "trading_system.accounts",
    "trading_system.notifications",
    "trading_system.analytics",
    "trading_system.persistence",
    "trading_system.result",
    # CR-017 Phase B — the backtest JobQueue worker bridges into the
    # public main.run entry point (same as trading_system/cli.py).
    # Deferred-imported so the routes never pull in runtime internals
    # at module load time; only the worker child process pays the cost.
    "trading_system.main",
)

_FORBIDDEN_PROJECT_IMPORT_PREFIXES = (
    "trading_system.execution",
    "trading_system.safety",
    "trading_system.risk",
    "trading_system.strategy_lab",
    "trading_system.data",
    "trading_system.backtesting",
)

# CR-019 carve-out: ``webapp/runtimes/`` is the documented
# composition layer that wraps the existing simulation surface
# (LocalBrokerAdapter + Portfolio + Backtest engine pieces) with
# a live data feed for paper-trading mode (REQ_F_PAP_001 /
# REQ_SDS_WEB2_004 / REQ_SDD_WEB2_003). Reach into
# ``execution.*`` / ``data.*`` / ``backtesting.*`` is part of the
# documented architecture; the routes layer remains tight (the
# router-specific audit below still bans those imports for
# ``webapp/routers/``).
_RUNTIMES_ALLOWED_EXTRA_PREFIXES = (
    "trading_system.execution",
    "trading_system.backtesting",
    "trading_system.data",
    "trading_system.portfolio",
    "trading_system.tax",
    "trading_system.strategies",
    # CR-019 step 1 (b) follow-up — building MarketState for the
    # live strategy step needs ``ScoredStock`` + ``ScoreBreakdown``
    # (pure dataclasses, no decisioning logic).
    "trading_system.screener",
)


def _python_files() -> list[Path]:
    return sorted(_PACKAGE_DIR.rglob("*.py"))


def _is_runtimes_file(py_file: Path) -> bool:
    """REQ_F_PAP_001 carve-out — files under
    ``trading_system/webapp/runtimes/`` are exempted from the
    routers-tight import-graph audit because the runtime layer
    is the documented composition point for paper trading (and,
    later, live trading)."""
    try:
        rel = py_file.relative_to(_PACKAGE_DIR)
    except ValueError:
        return False
    return rel.parts[:1] == ("runtimes",)


# ---------------------------------------------------------------------------
# TC_FAS_010 — import-graph audit
# ---------------------------------------------------------------------------


def test_webapp_import_graph_closed() -> None:
    """REQ_SDD_FAS_001 — every project-local import SHALL match an
    allow-listed prefix; ``execution`` / ``safety`` / ``risk`` /
    ``strategy_lab`` / ``data`` / ``backtesting`` SHALL NOT appear
    outside the documented CR-019 runtime composition layer
    (``webapp/runtimes/``)."""
    for py_file in _python_files():
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        is_runtime = _is_runtimes_file(py_file)
        allowed = _ALLOWED_PROJECT_IMPORT_PREFIXES
        if is_runtime:
            allowed = allowed + _RUNTIMES_ALLOWED_EXTRA_PREFIXES
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if not module.startswith("trading_system."):
                    continue
                if not is_runtime:
                    for forbidden in _FORBIDDEN_PROJECT_IMPORT_PREFIXES:
                        assert not module.startswith(forbidden), (
                            f"{py_file.relative_to(_REPO_ROOT)} imports {module} — "
                            f"REQ_SDD_FAS_001 forbids {forbidden}.*"
                        )
                # The runtime carve-out is documented in
                # ``_RUNTIMES_ALLOWED_EXTRA_PREFIXES``. ``safety`` /
                # ``risk`` / ``strategy_lab`` stay forbidden even
                # inside the runtime layer (the live-trading
                # amendment will revisit the risk-engine slot
                # explicitly).
                if is_runtime:
                    runtime_still_forbidden = (
                        "trading_system.safety",
                        "trading_system.risk",
                        "trading_system.strategy_lab",
                    )
                    for forbidden in runtime_still_forbidden:
                        assert not module.startswith(forbidden), (
                            f"{py_file.relative_to(_REPO_ROOT)} imports "
                            f"{module} — REQ_SDD_FAS_001 forbids "
                            f"{forbidden}.* even in the runtime layer"
                        )
                assert any(
                    module.startswith(p) for p in allowed
                ), (
                    f"{py_file.relative_to(_REPO_ROOT)} imports {module} — "
                    "not in the closed allow-list (REQ_SDD_FAS_001)"
                )


# ---------------------------------------------------------------------------
# TC_FAS_011 — no-Node structural audit
# ---------------------------------------------------------------------------


def test_no_node_toolchain_present() -> None:
    """REQ_SDS_FAS_003 — repository-wide grep rejects Node toolchain
    artefacts. ``htmx.min.js`` + ``htmx-sse.min.js`` are bundled
    under ``static/`` (the SSE file lands in Phase B; the placeholder
    presence is enforced once it ships)."""
    forbidden = {
        "node_modules",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "vite.config.js",
        "vite.config.ts",
        "webpack.config.js",
        "tsconfig.json",
    }
    for path in _REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        # Skip git internals + the wiki submodule + the venv.
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if rel.startswith((".git/", "Documentations/", ".venv/", "venv/")):
            continue
        assert path.name not in forbidden, (
            f"REQ_SDS_FAS_003 violated: {rel} present in the repo"
        )


def test_htmx_placeholder_present_under_static() -> None:
    assert (_STATIC_DIR / "htmx.min.js").is_file(), (
        f"htmx.min.js missing under {_STATIC_DIR.relative_to(_REPO_ROOT)}"
    )


def test_templates_present_under_templates_dir() -> None:
    """Jinja2 templates SHALL live under ``trading_system/webapp/templates/``
    so ``Jinja2Templates(directory=...)`` resolves at runtime."""
    assert (_TEMPLATES_DIR / "base.html").is_file()
    assert (_TEMPLATES_DIR / "dashboard.html").is_file()


# ---------------------------------------------------------------------------
# Routers expose factory closures, not inline decisioning code
# ---------------------------------------------------------------------------


def test_routers_do_not_import_decisioning_modules() -> None:
    """Repeats the audit at finer granularity — the routers + views
    SHALL go through Protocol-shaped slots attached to
    ``app.state``, never reach for the concrete promoter / reader /
    risk engine directly."""
    routers_dir = _PACKAGE_DIR / "routers"
    for py_file in routers_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                assert not module.startswith(
                    (
                        "trading_system.persistence.connection",
                        "trading_system.persistence.repositories",
                    )
                ), (
                    f"{py_file.relative_to(_REPO_ROOT)} imports {module} — "
                    "routers SHALL go through Protocol slots, not "
                    "concrete repositories (REQ_SDD_FAS_001)"
                )
