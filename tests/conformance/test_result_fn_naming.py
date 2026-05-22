"""Result-returning function naming — REQ_SDD_NAM_004.

REQ_SDD_NAM_004 — Result-returning function names SHALL describe
the outcome (``trade_passes_gate``, ``must_halt``, ``decompose``);
functions returning ``Result`` SHALL NOT be named with side-effect
verbs.

The audit walks every ``.py`` file in ``trading_system/``, finds
function definitions whose return annotation is ``Result[T, E]``,
and checks the name doesn't start with a side-effect verb. A small
allow-list documents the genuine boundary operations where the
function inherently has a side effect (broker submit, persistence
write, etc.) — those return Result by necessity, and renaming them
to outcome-style ("submission_result_for_order") would obscure
the call-site intent. The allow-list IS the audit's record of
"we know this is an exception".
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RUNTIME_DIR = _REPO_ROOT / "trading_system"


# Side-effect verb prefixes a Result-returning function SHOULD NOT
# start with. Each prefix maps to a documented outcome-style
# alternative the codebase prefers.
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "set_",
    "write_",
    "update_",
    "delete_",
    "clear_",
    "add_",
    "push_",
    "pop_",
    "mark_",
    "dispatch_",
    "emit_",
    "fire_",
)


# Documented exceptions — functions returning Result that genuinely
# perform a side effect at a system boundary. Renaming would obscure
# the call-site intent. Each entry is the qualified name
# ``<module-relative-path>:<function-name>``.
#
# To extend this allow-list: add the entry + document the reason
# inline. A wiki re-approval row per REQ_NF_LIF_002 records the
# decision.
_ALLOWED_BOUNDARY_OPS: frozenset[str] = frozenset({
    # CR-008 persistence — every write returns Result; the operation
    # is named after WHAT is persisted, which often reads as a
    # noun-or-verb depending on the aggregate. "record_*" is the
    # repo's convention for "append a row" and is allowed.
    "persistence/repositories/approvals.py:record_request",
    "persistence/repositories/approvals.py:record_response",
    "persistence/repositories/backtest_jobs.py:record_transition",
    "persistence/repositories/quant.py:record_transition",
    "persistence/repositories/transition.py:record",
    # CR-006 / CR-008 — submit returns Result by necessity (broker
    # adapter contract). The "submit" verb is canonical broker API
    # vocabulary; renaming would diverge from REQ_F_BRK_002.
    "execution/local.py:submit",
    "execution/adapter.py:submit",
    # Notification / channel delivery — async + side-effect (network)
    # so Result is the failure-reporting boundary. "deliver" matches
    # REQ_F_NOT_001 channel-Protocol vocabulary.
    "notifications/channels/slack.py:deliver",
    "notifications/channels/email.py:deliver",
    "notifications/channels/local_log.py:deliver",
    # CR-017 webapp — submit a job is the queueing operation; the
    # Result is success-or-error of enqueue, not of the run itself.
    "webapp/job_queue.py:submit",
    # CR-004 webui submit handler — wraps the JobQueue Protocol; the
    # surface name follows the JSON API verb.
    "webui/routes/backtests.py:submit",
    # CR-016 report writer — emits 5 files to disk; the "write"
    # verb matches REQ_F_RPT_001's "SHALL emit a report directory"
    # vocabulary and is the canonical operator-tooling call site.
    "analytics/report.py:write_report",
    # CR-002 / strategy_lab registry — stateful in-memory registry
    # of validated strategies. ``mark_validated`` / ``set_baseline``
    # are the documented state-transition operations on the
    # registry surface (REQ_F_MTO_006 strict-improvement
    # comparator); both return Result for the predictable
    # not-found / already-set rejection paths.
    "strategy_lab/registry.py:mark_validated",
    "strategy_lab/registry.py:set_baseline",
})


def _qualified(path: Path, name: str) -> str:
    return f"{path.relative_to(_RUNTIME_DIR).as_posix()}:{name}"


def _return_is_result(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True when ``node``'s return annotation begins with the
    ``Result`` type (``Result[T, E]`` or unsubscripted ``Result``)."""
    ret = node.returns
    if ret is None:
        return False
    # Strip string annotation if present (``from __future__ import
    # annotations`` makes everything a string).
    text = ast.unparse(ret) if hasattr(ast, "unparse") else None
    if text is None:
        return False
    # Match ``Result[...]`` or ``Result`` (bare); also tolerate the
    # qualified ``trading_system.result.Result[...]`` form.
    head = text.split("[", 1)[0].strip()
    return head.endswith("Result")


def test_result_returning_functions_avoid_side_effect_verbs() -> None:
    """REQ_SDD_NAM_004 — every Result-returning function name
    SHALL describe the outcome. Functions starting with a
    documented side-effect verb prefix are violations unless they
    appear in the explicit boundary-ops allow-list."""
    violations: list[str] = []
    for py_file in _RUNTIME_DIR.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith("_"):
                # Private helpers are exempt; their callers are
                # already inside the module.
                continue
            if not _return_is_result(node):
                continue
            qualified = _qualified(py_file, node.name)
            if qualified in _ALLOWED_BOUNDARY_OPS:
                continue
            for prefix in _FORBIDDEN_PREFIXES:
                if node.name.startswith(prefix):
                    violations.append(qualified)
                    break
    assert not violations, (
        "REQ_SDD_NAM_004 — Result-returning functions SHALL NOT "
        "use side-effect verb prefixes (rename to outcome-style "
        "or add to the boundary-ops allow-list with rationale):\n  "
        + "\n  ".join(violations)
    )


def test_audit_covers_at_least_one_result_returning_function() -> None:
    """Sanity guard — the audit's import-graph walker SHALL find
    at least one Result-returning function. A future refactor
    that strips ``Result`` from every annotation would silently
    pass the main audit; this companion test makes that visible."""
    found = 0
    for py_file in _RUNTIME_DIR.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if _return_is_result(node):
                found += 1
    assert found > 20, (
        f"only {found} Result-returning functions found; audit "
        "may have an annotation-parsing bug"
    )
