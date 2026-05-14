"""``Connection`` — the **only** SQLite caller in the codebase.

Wraps a single ``sqlite3.Connection`` with the pinned PRAGMA set:
``journal_mode=WAL``, ``synchronous=NORMAL``, ``foreign_keys=ON``,
plus a configurable ``busy_timeout`` (default 5 s).

REQ refs:
- REQ_F_PER_001 — single-file DB; auto-create parent directory.
- REQ_F_PER_010 — ``sqlite3`` import boundary; engine modules
  never import sqlite3 themselves.
- REQ_SDD_PER_001 — PRAGMA order pinned; construction failures
  surface as ``Err("persistence:open_failed:<path>:<reason>")``.
- REQ_SDS_PER_004 — WAL + many-reader / single-writer pattern;
  ``busy_timeout`` translates SQLite's ``SQLITE_BUSY`` to a
  bounded wait.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from trading_system.result import Err, Ok, Result

# Re-exported exception types so repositories + the migration
# runner can ``except`` them without importing sqlite3 directly
# (REQ_F_PER_010 / REQ_SDD_PER_001). The boundary lives here.
DatabaseError = sqlite3.Error
IntegrityError = sqlite3.IntegrityError
OperationalError = sqlite3.OperationalError

_DEFAULT_BUSY_TIMEOUT_MS = 5_000


@dataclass(slots=True)
class Connection:
    """Thin wrapper around ``sqlite3.Connection``.

    Construct via the classmethod ``Connection.open(...)`` rather
    than the dataclass constructor — ``open`` is the only call that
    sets up the PRAGMAs and catches the categorised ``Err``.
    """

    db_path: Path
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS
    _raw: sqlite3.Connection | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def open(
        cls,
        db_path: Path,
        *,
        busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
    ) -> Result[Connection, str]:
        """Open a SQLite connection with the canonical PRAGMA set.

        Auto-creates the parent directory so a fresh deployment
        works against any path the operator chose. PRAGMA application
        is part of construction — a failure to set ``journal_mode``
        or ``foreign_keys`` SHALL surface as ``Err`` rather than a
        half-configured connection.
        """
        if busy_timeout_ms <= 0:
            return Err(
                f"persistence:bad_config:busy_timeout_ms must be > 0, got {busy_timeout_ms}"
            )
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return Err(f"persistence:open_failed:{db_path}:mkdir:{e}")
        try:
            raw = sqlite3.connect(
                str(db_path),
                isolation_level=None,
                timeout=busy_timeout_ms / 1000,
            )
            raw.row_factory = sqlite3.Row
            # PRAGMA order is pinned per REQ_SDD_PER_001.
            raw.execute("PRAGMA journal_mode=WAL")
            raw.execute("PRAGMA synchronous=NORMAL")
            raw.execute("PRAGMA foreign_keys=ON")
            raw.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        except sqlite3.Error as e:
            return Err(f"persistence:open_failed:{db_path}:{e}")
        return Ok(cls(db_path=db_path, busy_timeout_ms=busy_timeout_ms, _raw=raw))

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    @property
    def raw(self) -> sqlite3.Connection:
        """Return the underlying connection. Repository code SHALL
        use this exclusively — no other module in the codebase
        imports ``sqlite3`` (REQ_F_PER_010)."""
        assert self._raw is not None, "Connection used after close()"
        return self._raw

    def execute(self, sql: str, params: tuple | dict | None = None) -> sqlite3.Cursor:
        """Convenience pass-through to ``raw.execute`` so callers
        avoid pulling ``sqlite3.Cursor`` into their own type stacks."""
        if params is None:
            return self.raw.execute(sql)
        return self.raw.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: list) -> sqlite3.Cursor:
        return self.raw.executemany(sql, seq_of_params)

    def begin_immediate(self) -> None:
        """Start a write-locked transaction. Use this around every
        repository write so a concurrent writer waits per
        ``busy_timeout`` rather than racing (REQ_F_PER_003 /
        REQ_SDD_PER_002)."""
        self.raw.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self.raw.execute("COMMIT")

    def rollback(self) -> None:
        self.raw.execute("ROLLBACK")

    def close(self) -> None:
        if self._raw is not None:
            self._raw.close()
            self._raw = None

    # ------------------------------------------------------------------
    # PRAGMA introspection (used by TC_PER_001)
    # ------------------------------------------------------------------

    def pragma(self, name: str) -> object:
        cursor = self.raw.execute(f"PRAGMA {name}")
        row = cursor.fetchone()
        if row is None:
            return None
        return row[0]
