"""Regenerate the OpenAPI schema snapshot the CR-017 webapp tests
compare against.

Usage:

    .venv/bin/python tools/regenerate_openapi_snapshot.py

The script builds a Phase-A FastAPI app, serialises ``app.openapi()``
canonically, and overwrites
``tests/webapp/openapi_phase_a.expected.json``. Operators run this
**only after a deliberate schema change** — drift caught by
``tests/webapp/test_openapi_stability.py`` SHALL force a Test-Plan
re-approval row per REQ_NF_LIF_002.
"""

from __future__ import annotations

import json
from pathlib import Path

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.webapp import WebappState, create_app


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SNAPSHOT_PATH = _REPO_ROOT / "tests" / "webapp" / "openapi_phase_a.expected.json"


def build_snapshot() -> str:
    """Return the canonical-JSON form of the Phase-A schema."""
    verifier = AccountScopedTokenVerifier(secret=b"snapshot", ttl_seconds=3600)
    app = create_app(WebappState(token_verifier=verifier))
    schema = app.openapi()
    return json.dumps(schema, sort_keys=True, indent=2) + "\n"


def main() -> int:
    canonical = build_snapshot()
    _SNAPSHOT_PATH.write_text(canonical, encoding="utf-8")
    print(
        f"wrote {_SNAPSHOT_PATH.relative_to(_REPO_ROOT)} "
        f"({len(canonical)} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
