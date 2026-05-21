"""Project-wide naming-convention audits.

REQ refs:
- REQ_SDD_NAM_001 — Type names PascalCase; function/variable names
  snake_case; constants UPPER_SNAKE_CASE. Enforced primarily by Ruff
  (the ``N`` selector in ``pyproject.toml``). This test asserts that
  the Ruff config is wired so the lint step actually catches
  violations on every developer iteration.
- REQ_SDD_NAM_002 — Concrete adapter classes SHALL end in
  ``Adapter`` (e.g., ``LocalBrokerAdapter``). The matching
  Protocol/abstract types in the project's published surface
  (``BrokerAdapter``, ``BacktesterAdapter``, ``EvaluatorAdapter``)
  ARE the named Protocols per the SDD's own example — they
  predate this REQ and stay grandfathered. The audit enforces
  the *concrete* side: the boundary type for the broker has a
  concrete impl whose class name ends in ``Adapter``.
- REQ_SDD_NAM_003 — Configuration record types SHALL end in
  ``Config`` (e.g., ``ScreenerConfig``, ``RiskConfig``). The
  audit walks files in config-like locations and flags any
  uncategorised frozen dataclass that doesn't carry the suffix.
  Output / report / params / spec records are exempt by
  convention (they aren't "config records" in the REQ's sense).
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_RUNTIME_DIR = _REPO_ROOT / "trading_system"


# ---------------------------------------------------------------------------
# REQ_SDD_NAM_001 — case conventions via Ruff
# ---------------------------------------------------------------------------


def test_ruff_pep8_naming_selector_enabled() -> None:
    """REQ_SDD_NAM_001 — Ruff's ``N`` selector enforces PEP 8
    naming (PascalCase classes, snake_case funcs/vars,
    UPPER_SNAKE_CASE constants). The pyproject.toml MUST list
    ``N`` in ``[tool.ruff.lint] select`` so every developer
    iteration catches violations. If a future refactor drops
    the selector, this test fails and forces a re-approval."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    select = data.get("tool", {}).get("ruff", {}).get("lint", {}).get("select", [])
    assert "N" in select, (
        f"Ruff's pep8-naming selector ('N') MUST be in select; got {select!r}"
    )


# ---------------------------------------------------------------------------
# Shared AST helpers
# ---------------------------------------------------------------------------


def _walk_python_classes() -> list[tuple[Path, ast.ClassDef]]:
    """Yield every class definition in the runtime tree."""
    out: list[tuple[Path, ast.ClassDef]] = []
    for py_file in _RUNTIME_DIR.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                out.append((py_file, node))
    return out


def _bases_include(node: ast.ClassDef, names: set[str]) -> bool:
    """Return True if any of the class's bases matches one of
    ``names`` by its trailing identifier."""
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in names:
            return True
        if isinstance(base, ast.Attribute) and base.attr in names:
            return True
    return False


def _decorator_name(node: ast.expr) -> str:
    """Best-effort decorator-name extraction."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


# ---------------------------------------------------------------------------
# REQ_SDD_NAM_002 — concrete adapter convention
# ---------------------------------------------------------------------------


_BROKER_ADAPTER_CONCRETE = "LocalBrokerAdapter"


def test_local_broker_adapter_is_named_with_adapter_suffix() -> None:
    """REQ_SDD_NAM_002 — the only concrete BrokerAdapter shipped
    in the lifecycle is ``LocalBrokerAdapter`` (per CLAUDE.md).
    The class SHALL exist and carry the ``Adapter`` suffix so
    operators can identify the concrete impl at a glance."""
    found = False
    for _path, node in _walk_python_classes():
        if node.name == _BROKER_ADAPTER_CONCRETE:
            found = True
            assert node.name.endswith("Adapter"), (
                "REQ_SDD_NAM_002 — concrete adapter class missing 'Adapter' suffix"
            )
            # Concrete impls SHALL NOT inherit Protocol directly.
            assert not _bases_include(node, {"Protocol"}), (
                f"{node.name} inherits Protocol — should be concrete"
            )
            break
    assert found, (
        f"REQ_SDD_NAM_002 — concrete adapter "
        f"{_BROKER_ADAPTER_CONCRETE!r} not found anywhere in the runtime tree"
    )


def test_new_adapter_classes_are_concrete_not_protocols() -> None:
    """REQ_SDD_NAM_002 — *new* classes ending in ``Adapter`` SHALL
    be concrete. The three grandfathered Protocol Adapters
    (``BrokerAdapter``, ``BacktesterAdapter``, ``EvaluatorAdapter``)
    predate this REQ and stay in the allow-list; any *additional*
    Protocol ending in ``Adapter`` fails this audit.

    The allow-list is the audit's escape valve — extending it is a
    deliberate operator decision that requires a wiki re-approval
    row per REQ_NF_LIF_002. Don't expand silently."""
    GRANDFATHERED_PROTOCOL_ADAPTERS = {
        "BrokerAdapter",
        "BacktesterAdapter",
        "EvaluatorAdapter",
    }
    violations: list[str] = []
    for path, node in _walk_python_classes():
        if not node.name.endswith("Adapter"):
            continue
        if not _bases_include(node, {"Protocol"}):
            continue
        if node.name in GRANDFATHERED_PROTOCOL_ADAPTERS:
            continue
        rel = path.relative_to(_REPO_ROOT)
        violations.append(f"{rel}:{node.lineno}:{node.name}")
    assert not violations, (
        "New Protocol types ending in 'Adapter' SHALL NOT be "
        "introduced (REQ_SDD_NAM_002):\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# REQ_SDD_NAM_003 — Config suffix convention
# ---------------------------------------------------------------------------


_CONFIG_MODULE_HINTS = (
    "/config.py",
    "/config/",
    "/loader.py",
    "/yaml_loader.py",
)

# Suffixes that document the dataclass's role distinct from "config record".
# Output types (Report, Result), parameter sub-records (Params), and
# specs that load from operator-supplied YAML (Spec) stay readable
# without the ``Config`` rename; the REQ targets the configuration
# boundary specifically.
_EXEMPT_SUFFIXES = ("Report", "Result", "Params", "Spec", "Manifest", "Outcome")


def test_config_module_dataclasses_end_in_config() -> None:
    """REQ_SDD_NAM_003 — Configuration record types in the
    config-loading boundary SHALL end in ``Config``. The audit:

    1. Walks files in config-like locations (``config/``,
       ``*_loader.py``, ``config.py``).
    2. Skips Enums (value namespaces, not records).
    3. Skips classes whose name carries an exempt suffix
       (Report/Result/Params/Spec/Manifest/Outcome) — these
       document a non-config role explicitly.
    4. Skips private classes (``_``-prefixed).
    5. Flags every remaining frozen dataclass that doesn't carry
       the ``Config`` suffix.
    """
    violations: list[str] = []
    for path, node in _walk_python_classes():
        rel = path.relative_to(_REPO_ROOT)
        rel_str = "/" + str(rel)
        if not any(hint in rel_str for hint in _CONFIG_MODULE_HINTS):
            continue
        if node.name.startswith("_"):
            continue
        if _bases_include(node, {"Enum", "StrEnum", "IntEnum"}):
            continue
        is_dataclass = any(
            _decorator_name(d) == "dataclass" for d in node.decorator_list
        )
        if not is_dataclass:
            continue
        if any(node.name.endswith(s) for s in _EXEMPT_SUFFIXES):
            continue
        if not node.name.endswith("Config"):
            violations.append(f"{rel}:{node.lineno}:{node.name}")
    assert not violations, (
        "Configuration record types SHALL end in 'Config' "
        "(REQ_SDD_NAM_003):\n  " + "\n  ".join(violations)
    )
