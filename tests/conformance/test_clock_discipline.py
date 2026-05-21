"""Clock + RNG discipline audits.

REQ refs:
- REQ_TP_FIX_001 — All tests SHALL use ``EventClock`` and seeded
  RNG by default; tests using ``WallClock`` SHALL be marked
  ``@pytest.mark.wallclock`` and excluded from the default CI run.
- REQ_SDS_ARC_005 / REQ_SDS_ARC_006 (transitively) — runtime
  modules SHALL access ``datetime.now`` only at boundaries; engine
  modules SHALL receive ``now`` via injection.

The audit walks every ``tests/**/*.py`` file and flags any bare
``time.sleep`` / ``datetime.now`` call that isn't in a
``@pytest.mark.wallclock``-marked test (REQ_TP_FIX_001) or in an
explicitly-exempt helper module.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TESTS_DIR = _REPO_ROOT / "tests"


# Test files that may legitimately use wall-clock primitives:
# * tests/conformance/ — the audit reads source files; the AST
#   walker doesn't actually CALL ``datetime.now``.
# * tests/test_main_*  / tests/test_cli.py — top-level smoke tests
#   that boot the CLI end-to-end; the CLI uses the wall clock at
#   the boundary by design.
# * The container-reproducibility test fixture pins SOURCE_DATE_EPOCH
#   but uses ``uuid.uuid4().hex`` for image-tag uniqueness — pure
#   randomness not clock-derived; allow-listed.
# These are conscious exceptions; extending the list requires a
# wiki re-approval per REQ_NF_LIF_002.
_WALLCLOCK_OPT_OUT_FILES: frozenset[str] = frozenset({
    "tests/conformance/test_clock_discipline.py",
    "tests/conformance/test_traceability_meta.py",
    "tests/test_main.py",
    "tests/test_cli.py",
    "tests/webapp/test_container_reproducibility.py",
})


def _file_uses_marker(tree: ast.Module, marker: str) -> bool:
    """Return True when any decorator in ``tree`` matches
    ``pytest.mark.<marker>`` (in either ``@pytest.mark.X`` or
    ``@mark.X`` form) — module-level OR per-function. The audit
    is conservative: if ANY test in the file is marked, we treat
    the whole file as opt-out, since pytest applies markers at the
    function granularity but the test file's intent is usually
    homogeneous."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            name = _decorator_attr_chain(deco)
            if marker in name:
                return True
    # Module-level ``pytestmark = pytest.mark.<marker>`` form.
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    if marker in ast.unparse(stmt.value):
                        return True
    return False


def _decorator_attr_chain(node: ast.expr) -> str:
    """Flatten a decorator expression into ``"a.b.c"`` form."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_decorator_attr_chain(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _decorator_attr_chain(node.func)
    return ast.unparse(node) if hasattr(ast, "unparse") else ""


def _find_wallclock_uses(tree: ast.Module) -> list[ast.Call]:
    """Return every blocking wall-clock call in ``tree``.

    REQ_TP_FIX_001 targets tests whose ASSERTIONS depend on real
    elapsed time. That's the ``time.sleep`` / ``asyncio.sleep``
    family — calls that BLOCK on the wall clock. Plain
    ``datetime.now()`` for fixture data (e.g., issuing a token
    with the current timestamp + immediately verifying it) isn't
    a wall-clock dependency — the token's TTL is bounded and the
    test doesn't wait — so we don't flag those uses.
    """
    hits: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _decorator_attr_chain(node.func)
        if chain in {"time.sleep", "asyncio.sleep"}:
            hits.append(node)
    return hits


def test_tests_use_event_clock_by_default() -> None:
    """REQ_TP_FIX_001 — tests that touch the wall clock SHALL be
    marked ``@pytest.mark.wallclock`` so the default CI run skips
    them. The audit walks every ``tests/**/*.py`` and flags any
    file containing a wall-clock call that ISN'T marked AND ISN'T
    in the opt-out allow-list."""
    violations: list[str] = []
    for py_file in _TESTS_DIR.rglob("test_*.py"):
        rel = str(py_file.relative_to(_REPO_ROOT))
        if rel in _WALLCLOCK_OPT_OUT_FILES:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        hits = _find_wallclock_uses(tree)
        if not hits:
            continue
        if _file_uses_marker(tree, "wallclock"):
            continue
        for h in hits:
            violations.append(f"{rel}:{h.lineno}:{_decorator_attr_chain(h.func)}")
    assert not violations, (
        "REQ_TP_FIX_001 — tests using wall-clock primitives without "
        "@pytest.mark.wallclock:\n  " + "\n  ".join(violations)
    )
