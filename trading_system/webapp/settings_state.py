"""CR-032 — operator settings reload-pending state (REQ_SDD_SET_003).

The settings save handler sets ``app.state.reload_pending`` to a
fresh ``ReloadPending`` instance on every successful write. The
dashboard chrome reads the slot on every render + shows the
"reload pending" banner when non-``None``.

The slot is **in-memory only** — restart IS the reload (per the
CR-032 open-question #4 resolution). A surviving banner
post-restart would lie, so we deliberately don't persist this
to SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ReloadPending:
    """In-memory record of an outstanding settings save that
    hasn't been applied yet (the operator needs to restart the
    container or rerun the runtime to pick up the new config).

    Fields:
    - ``modified_at`` — when the save landed; renders in the
      banner so the operator sees how long the reload has been
      pending.
    - ``sections_changed`` — which sub-sections of the
      ``notifications.yaml`` (or any future YAML) got edited
      this round. Multiple saves can stack into a single
      pending entry — the latest save wins, but the
      ``sections_changed`` tuple aggregates the operator's
      touch surface across the session.
    - ``env_vars_referenced`` — env-var NAMES the saved config
      depends on. The banner template uses ``os.environ.get(
      name)`` server-side to render "set" / "unset" indicators
      so the operator verifies every required secret is
      exported before the restart (per REQ_NF_SET_001 secret
      discipline).
    """

    modified_at: datetime
    sections_changed: tuple[str, ...]
    env_vars_referenced: tuple[str, ...]
