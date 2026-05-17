"""TC_FAS_006 — OpenAPI schema-stability snapshot.

REQ refs:
- REQ_F_FAS_004 — OpenAPI schema auto-generated at ``/openapi.json``.
- REQ_SDD_FAS_006 — schema drift SHALL fail this test; operators
  regenerate the snapshot only via the explicit
  ``tools/regenerate_openapi_snapshot.py`` invocation. Any schema
  change forces a Test-Plan re-approval row per REQ_NF_LIF_002.
"""

from __future__ import annotations

import json
from pathlib import Path

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.webapp import WebappState, create_app


_SNAPSHOT_PATH = Path(__file__).resolve().parent / "openapi_phase_a.expected.json"


def test_snapshot_committed() -> None:
    assert _SNAPSHOT_PATH.is_file(), (
        f"openapi snapshot missing at {_SNAPSHOT_PATH.name}; regenerate via "
        "`python tools/regenerate_openapi_snapshot.py`"
    )


def test_openapi_schema_matches_committed_snapshot() -> None:
    """The live ``app.openapi()`` SHALL serialise byte-identically
    against the checked-in snapshot. Drift here means somebody
    changed an endpoint signature or response schema without
    regenerating — failing fast forces the Test-Plan re-approval
    flow."""
    verifier = AccountScopedTokenVerifier(secret=b"snapshot", ttl_seconds=3600)
    app = create_app(WebappState(token_verifier=verifier))
    live = json.dumps(app.openapi(), sort_keys=True, indent=2) + "\n"
    expected = _SNAPSHOT_PATH.read_text(encoding="utf-8")
    if live != expected:
        diff_hint = (
            "OpenAPI schema drift — regenerate the snapshot via "
            "`python tools/regenerate_openapi_snapshot.py` after "
            "deliberately changing an endpoint, and update the "
            "Test-Plan approval row per REQ_NF_LIF_002."
        )
        # Show a small contextual diff so the failure is actionable.
        live_lines = live.splitlines()
        expected_lines = expected.splitlines()
        differences: list[str] = []
        for i, (a, b) in enumerate(zip(expected_lines, live_lines, strict=False)):
            if a != b:
                differences.append(f"  line {i + 1}: expected={a!r} got={b!r}")
                if len(differences) >= 5:
                    break
        joined = "\n".join(differences) if differences else "<length mismatch only>"
        raise AssertionError(f"{diff_hint}\nFirst differing lines:\n{joined}")


def test_openapi_schema_documents_required_phase_a_paths() -> None:
    """Defence in depth — even if the snapshot exists, the schema
    SHALL expose the documented Phase-A endpoints."""
    verifier = AccountScopedTokenVerifier(secret=b"snapshot", ttl_seconds=3600)
    app = create_app(WebappState(token_verifier=verifier))
    schema = app.openapi()
    paths = set(schema["paths"].keys())
    expected_subset = {
        "/health",
        "/api/accounts/{account_id}/live-state",
        "/api/registry/{strategy_id}/promote",
        "/",
    }
    missing = expected_subset - paths
    assert not missing, f"OpenAPI schema missing {missing}"
