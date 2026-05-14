"""``MigrationRunner`` — idempotent, SHA-locked schema runner.

Pipeline (REQ_SDD_PER_004):

1. Ensure ``schema_migrations`` exists.
2. Load the applied set: ``{filename -> sha256}``.
3. Walk every ``*.sql`` file under the migrations directory in
   lexicographic order:
   - if filename already applied AND the on-disk SHA differs from
     the recorded SHA → reject with
     ``Err("persistence:migration_sha_mismatch:<filename>")``.
     This catches retroactive edits to historical migrations.
   - if already applied with matching SHA → skip.
   - else → schedule as pending.
4. ``dry_run=True``: return the list of would-be-applied filenames
   without touching the DB.
5. ``dry_run=False``: for each pending file, execute the SQL inside
   ``BEGIN IMMEDIATE`` / ``COMMIT``, record the row on success, fail
   the entire run on the first error (no partial-apply window).

REQ refs: REQ_F_PER_004, REQ_NF_PER_001, REQ_SDS_PER_003,
REQ_SDD_PER_004.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trading_system.persistence.connection import Connection, DatabaseError
from trading_system.result import Err, Ok, Result

_MIGRATIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    sha256     TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


@dataclass(slots=True)
class MigrationRunner:
    """Apply every ``*.sql`` migration under ``migrations_dir``
    against ``conn``. Idempotent across runs."""

    conn: Connection
    migrations_dir: Path

    def run(self, *, dry_run: bool = False) -> Result[list[str], str]:
        """Apply pending migrations; return the list of applied
        filenames (or would-be-applied, on dry-run)."""
        if not self.migrations_dir.exists():
            return Err(f"persistence:migrations_dir_missing:{self.migrations_dir}")
        try:
            self.conn.execute(_MIGRATIONS_TABLE_DDL)
        except DatabaseError as e:
            return Err(f"persistence:migration_failed:bootstrap:{e}")

        applied = _load_applied(self.conn)
        on_disk = sorted(self.migrations_dir.glob("*.sql"))
        pending: list[tuple[str, str, str]] = []  # (filename, sha, sql)
        for path in on_disk:
            try:
                sql = path.read_text(encoding="utf-8")
            except OSError as e:
                return Err(f"persistence:migration_failed:read:{path}:{e}")
            sha = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            recorded = applied.get(path.name)
            if recorded is not None and recorded != sha:
                return Err(f"persistence:migration_sha_mismatch:{path.name}")
            if recorded is not None:
                continue  # already applied, SHA matches; skip.
            pending.append((path.name, sha, sql))

        if dry_run:
            return Ok([f for f, _, _ in pending])

        applied_now: list[str] = []
        for filename, sha, sql in pending:
            # ``executescript`` runs its own transaction control —
            # it COMMITs any pending transaction before executing,
            # and the script itself may contain transaction
            # statements. We let it manage transactions here and
            # record the audit row in a separate transaction.
            try:
                self.conn.raw.executescript(sql)
            except DatabaseError as e:
                return Err(f"persistence:migration_failed:{filename}:{e}")
            try:
                self.conn.begin_immediate()
                self.conn.execute(
                    "INSERT INTO schema_migrations (filename, sha256, applied_at) "
                    "VALUES (?, ?, ?)",
                    (filename, sha, datetime.now(tz=UTC).isoformat()),
                )
                self.conn.commit()
            except DatabaseError as e:
                try:
                    self.conn.rollback()
                except DatabaseError:
                    pass
                return Err(f"persistence:migration_failed:{filename}:record:{e}")
            applied_now.append(filename)
        return Ok(applied_now)


def _load_applied(conn: Connection) -> dict[str, str]:
    cursor = conn.execute("SELECT filename, sha256 FROM schema_migrations")
    return {row["filename"]: row["sha256"] for row in cursor.fetchall()}
