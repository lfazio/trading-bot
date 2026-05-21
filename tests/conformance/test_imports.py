"""Import-graph + module-layout conformance audits.

REQ refs:
- REQ_SDD_IMP_001 — Source-tree layout SHALL match the SDS module
  decomposition. Every documented top-level package SHALL exist as
  a directory under ``trading_system/``.
- REQ_SDD_IMP_002 — Each top-level package SHALL declare ``__all__``
  in its ``__init__.py``.
- REQ_SDD_IMP_003 — The dependency graph between top-level packages
  SHALL be acyclic.
- REQ_SDD_IMP_004 — Each top-level package's ``__init__.py``
  docstring SHALL reference at least one REQ id (the module's
  authority comes from the lifecycle).
- REQ_SDS_FLO_004 — Runtime SHALL NOT invoke ``strategy_lab/``
  modules other than the registry; meta-optimization runs SHALL
  be operator-triggered, not runtime loop calls.

Audits are AST-only — fast, deterministic, no I/O. The output is a
diagnostic list of every offending file so a future refactor knows
exactly what to fix.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict, deque
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RUNTIME_DIR = _REPO_ROOT / "trading_system"


# Top-level packages that MUST exist per the SDS module decomposition.
# Sourced from CLAUDE.md + SDS §3; matches the actual on-disk layout.
_REQUIRED_PACKAGES: frozenset[str] = frozenset({
    "accounts",
    "analytics",
    "backtesting",
    "capital_flow",
    "config",
    "dashboard",
    "data",
    "execution",
    "milestone_controller",
    "models",
    "notifications",
    "persistence",
    "phase_engine",
    "portfolio",
    "risk",
    "safety",
    "screener",
    "strategies",
    "strategy_lab",
    "structured_products",
    "tax",
    "turbo_selector",
})


# ---------------------------------------------------------------------------
# REQ_SDD_IMP_001 — module layout matches SDS
# ---------------------------------------------------------------------------


def test_every_required_package_exists() -> None:
    """REQ_SDD_IMP_001 — every package named in the SDS module
    decomposition SHALL exist as a top-level directory under
    ``trading_system/``."""
    missing: list[str] = []
    for pkg in sorted(_REQUIRED_PACKAGES):
        path = _RUNTIME_DIR / pkg
        if not (path.is_dir() and (path / "__init__.py").exists()):
            missing.append(pkg)
    assert not missing, (
        f"REQ_SDD_IMP_001 — missing top-level packages: {missing}"
    )


# ---------------------------------------------------------------------------
# REQ_SDD_IMP_002 — __all__ declared
# ---------------------------------------------------------------------------


def _has_dunder_all(init_path: Path) -> bool:
    """Return True if ``init_path`` declares a module-level ``__all__``."""
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return True
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                return True
    return False


def test_every_required_package_declares_all() -> None:
    """REQ_SDD_IMP_002 — every required top-level package's
    ``__init__.py`` SHALL declare ``__all__``."""
    violations: list[str] = []
    for pkg in sorted(_REQUIRED_PACKAGES):
        init = _RUNTIME_DIR / pkg / "__init__.py"
        if not _has_dunder_all(init):
            violations.append(pkg)
    assert not violations, (
        "REQ_SDD_IMP_002 — packages missing __all__: " + ", ".join(violations)
    )


# ---------------------------------------------------------------------------
# REQ_SDD_IMP_004 — REQ ids referenced in package docstrings
# ---------------------------------------------------------------------------


_REQ_PATTERN = re.compile(r"REQ_[A-Z_]+_[0-9]+")


def _package_docstring(init_path: Path) -> str:
    """Return the module-level docstring of ``init_path`` (empty
    string if absent or syntactically broken)."""
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    except SyntaxError:
        return ""
    return ast.get_docstring(tree) or ""


def test_every_required_package_references_a_req_id() -> None:
    """REQ_SDD_IMP_004 — every required top-level package's
    ``__init__.py`` docstring SHALL reference at least one REQ id.
    The reference can live anywhere in the docstring (a list, a
    paragraph, an inline note); the audit just looks for the
    pattern ``REQ_[A-Z_]+_[0-9]+``."""
    violations: list[str] = []
    for pkg in sorted(_REQUIRED_PACKAGES):
        init = _RUNTIME_DIR / pkg / "__init__.py"
        doc = _package_docstring(init)
        if not _REQ_PATTERN.search(doc):
            violations.append(pkg)
    assert not violations, (
        "REQ_SDD_IMP_004 — packages with no REQ id in __init__ "
        "docstring: " + ", ".join(violations)
    )


# ---------------------------------------------------------------------------
# REQ_SDD_IMP_003 — acyclic import graph
# ---------------------------------------------------------------------------


def _top_level_package_of(module: str) -> str | None:
    """Return ``module``'s first segment under ``trading_system.``,
    or ``None`` for non-runtime imports."""
    if not module.startswith("trading_system."):
        return None
    parts = module.split(".")
    # ``trading_system.<pkg>.<...>`` -> "<pkg>"
    if len(parts) >= 2:
        return parts[1]
    return None


def _build_package_import_graph() -> dict[str, set[str]]:
    """Return ``{pkg: {other_pkg, ...}}`` of inter-package imports
    under ``trading_system/``."""
    graph: dict[str, set[str]] = defaultdict(set)
    for py_file in _RUNTIME_DIR.rglob("*.py"):
        parts = py_file.relative_to(_RUNTIME_DIR).parts
        if not parts:
            continue
        owner = parts[0] if parts[0] != "__init__.py" else None
        if owner is None or owner.endswith(".py"):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = _top_level_package_of(node.module or "")
                if target and target != owner:
                    graph[owner].add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = _top_level_package_of(alias.name)
                    if target and target != owner:
                        graph[owner].add(target)
    return graph


def _detect_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return every elementary cycle in ``graph`` (Tarjan-style
    DFS). For the project's ~25-node graph the brute-force walker
    is fast enough; we don't reach for ``networkx`` to keep the
    runtime audit dependency-free."""
    cycles: list[list[str]] = []
    visited: set[str] = set()
    # Track the current DFS path to detect back-edges.
    path: list[str] = []
    on_path: set[str] = set()

    def dfs(node: str) -> None:
        if node in on_path:
            # Back edge — extract the cycle starting at node.
            start = path.index(node)
            cycles.append(path[start:] + [node])
            return
        if node in visited:
            return
        on_path.add(node)
        path.append(node)
        for nbr in sorted(graph.get(node, set())):
            dfs(nbr)
        path.pop()
        on_path.discard(node)
        visited.add(node)

    for src in sorted(graph):
        dfs(src)
    return cycles


def test_runtime_package_import_graph_is_acyclic() -> None:
    """REQ_SDD_IMP_003 — the dependency graph between top-level
    packages SHALL be acyclic. The audit builds the package-to-
    package edge set from every ``from trading_system.<pkg>``
    import in the runtime tree and walks for cycles."""
    graph = _build_package_import_graph()
    cycles = _detect_cycles(graph)
    if cycles:
        formatted = "; ".join(" -> ".join(c) for c in cycles)
        raise AssertionError(
            f"REQ_SDD_IMP_003 — import-graph cycles detected: {formatted}"
        )


# ---------------------------------------------------------------------------
# REQ_SDS_FLO_004 — runtime SHALL NOT invoke strategy_lab except registry
# ---------------------------------------------------------------------------


_STRATEGY_LAB_RUNTIME_ALLOWED_PATHS = (
    # The registry is the only piece of strategy_lab that the trading
    # loop reaches directly (read-only — fetch validated strategies).
    "trading_system.strategy_lab.registry",
)

# Off-the-trading-path callers that import strategy_lab as a
# type-only dependency. These are documented exceptions:
# * config/validator.py — loads quant.yaml's offline-only schema at
#   startup; the import is purely shape-validation, not runtime
#   invocation. CR-002 follow-up.
# * persistence/mappers.py — serialises ``StrategyMetrics`` rows; the
#   import is a dataclass typing reference, not a runtime call.
# * persistence/repositories/quant.py — the CR-008 SQLite backend
#   for the offline HypothesisRepository; satisfies the same
#   Protocol as the in-memory variant. CR-002 follow-up.
# Extending this allow-list is a deliberate operator decision and
# requires a wiki re-approval per REQ_NF_LIF_002.
_STRATEGY_LAB_TYPE_ONLY_CALLERS = frozenset({
    "trading_system/config/validator.py",
    "trading_system/persistence/mappers.py",
    "trading_system/persistence/repositories/quant.py",
})


def test_runtime_does_not_import_strategy_lab_outside_registry() -> None:
    """REQ_SDS_FLO_004 — only ``strategy_lab.registry`` is allowed
    on the runtime path; everything else under ``strategy_lab/``
    is meta-optimization machinery and SHALL be invoked
    out-of-band (CLI tooling), never from the trading loop.

    Type-only off-path callers (config validator, persistence
    mappers / quant repo) are in the closed allow-list above.
    """
    violations: list[str] = []
    for py_file in _RUNTIME_DIR.rglob("*.py"):
        if py_file.is_relative_to(_RUNTIME_DIR / "strategy_lab"):
            continue
        rel_path_str = str(py_file.relative_to(_REPO_ROOT))
        if rel_path_str in _STRATEGY_LAB_TYPE_ONLY_CALLERS:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if not module.startswith("trading_system.strategy_lab"):
                    continue
                if any(module.startswith(a) for a in _STRATEGY_LAB_RUNTIME_ALLOWED_PATHS):
                    continue
                rel = py_file.relative_to(_REPO_ROOT)
                violations.append(f"{rel}:{node.lineno}:{module}")
    assert not violations, (
        "REQ_SDS_FLO_004 — runtime imports strategy_lab outside the "
        "registry boundary:\n  " + "\n  ".join(violations)
    )


# Silence the unused-import warning Pyright surfaces for ``deque`` —
# kept as a future-proof helper if the cycle detector needs an
# iterative rewrite; remove when actually needed.
_ = deque
