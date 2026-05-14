"""Tests for ``trading_system.persistence.connection``.

Covers TC_PER_001 (PRAGMAs + multi-reader open) and TC_PER_011
(import-graph audit: only the ``trading_system.persistence``
layer imports sqlite3; engine modules go through ``Connection``).

REQ refs: REQ_F_PER_001, REQ_F_PER_010, REQ_SDD_PER_001,
REQ_SDS_PER_001, REQ_SDS_PER_004.
"""

from __future__ import annotations

import ast
from pathlib import Path

from trading_system.persistence.connection import Connection
from trading_system.result import Err, Ok


# ---------------------------------------------------------------------------
# TC_PER_001 — PRAGMAs + multi-reader open
# ---------------------------------------------------------------------------


def test_open_sets_canonical_pragmas(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    res = Connection.open(db, busy_timeout_ms=2500)
    match res:
        case Ok(conn):
            pass
        case Err(e):
            raise AssertionError(f"unexpected Err: {e}")
    assert conn.pragma("journal_mode") == "wal"
    assert conn.pragma("foreign_keys") == 1
    assert conn.pragma("busy_timeout") == 2500
    conn.close()


def test_open_auto_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "state.sqlite"
    res = Connection.open(nested)
    assert isinstance(res, Ok)
    assert nested.parent.is_dir()
    res.value.close()


def test_two_readers_can_coexist(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    r1 = Connection.open(db).unwrap()
    r2 = Connection.open(db).unwrap()
    # Each reads PRAGMAs independently without locking the other.
    assert r1.pragma("journal_mode") == "wal"
    assert r2.pragma("journal_mode") == "wal"
    r1.close()
    r2.close()


def test_bad_busy_timeout_rejected(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    match Connection.open(db, busy_timeout_ms=0):
        case Err(reason):
            assert reason.startswith("persistence:bad_config")
        case Ok(_):
            raise AssertionError("expected Err")


def test_close_idempotent(tmp_path: Path) -> None:
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    conn.close()
    conn.close()  # second close is a no-op, not a panic


# ---------------------------------------------------------------------------
# TC_PER_011 — only the persistence layer imports sqlite3
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PERSISTENCE_LAYER = _REPO_ROOT / "trading_system" / "persistence"


def _imports_sqlite3(path: Path) -> bool:
    """Return True iff ``path`` contains an ``import sqlite3`` /
    ``from sqlite3 import ...`` anywhere in its AST."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3":
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "sqlite3":
                return True
    return False


def test_only_persistence_layer_imports_sqlite3() -> None:
    """REQ_F_PER_010 — the *persistence layer* is the only component
    that may import sqlite3. The layer is the package, so any file
    under ``trading_system/persistence/`` is allowed; every other
    module must go through ``Connection``."""
    offenders: list[Path] = []
    trading_system_root = _REPO_ROOT / "trading_system"
    for py_file in trading_system_root.rglob("*.py"):
        try:
            py_file.relative_to(_PERSISTENCE_LAYER)
            continue  # inside the persistence layer — allowed
        except ValueError:
            pass
        if _imports_sqlite3(py_file):
            offenders.append(py_file.relative_to(_REPO_ROOT))
    assert not offenders, (
        "Only the trading_system.persistence layer may import sqlite3 "
        f"(REQ_F_PER_010). Offenders: {[str(p) for p in offenders]}"
    )
