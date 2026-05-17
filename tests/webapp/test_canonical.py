"""TC_FAS_002 — canonical-JSON response determinism.

REQ refs: REQ_NF_FAS_001, REQ_SDS_FAS_002, REQ_SDD_FAS_002.

The FastAPI surface routes serialisation through the project-wide
``notifications.canonical.canonical_json_line`` helper so the bytes
match the stdlib webui's output exactly. Two calls with equal inputs
SHALL produce byte-identical bodies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.models.identifiers import AccountId
from trading_system.models.phase import Phase
from trading_system.models.safety import KillSwitchState
from trading_system.webapp.canonical import (
    canonical_error_response,
    canonical_json_response,
)
from trading_system.webui.schemas import LiveStateResponse


_NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def _live_state() -> LiveStateResponse:
    return LiveStateResponse(
        account_id=AccountId("default"),
        as_of=_NOW,
        ks_state=KillSwitchState.ACTIVE,
        phase=Phase(1),
        open_positions_count=3,
        equity_after_tax=Decimal("12345.67"),
    )


def test_canonical_response_body_is_byte_identical_across_calls() -> None:
    a = canonical_json_response(_live_state())
    b = canonical_json_response(_live_state())
    assert a.body == b.body  # raw bytes


def test_canonical_response_keys_sorted() -> None:
    """REQ_SDD_FAS_002 — ``sort_keys=True`` enforced via the
    canonical-JSON serialiser."""
    response = canonical_json_response(_live_state())
    text = response.body.decode("utf-8")
    keys_in_order = [
        '"account_id"',
        '"as_of"',
        '"equity_after_tax"',
        '"ks_state"',
        '"open_positions_count"',
        '"phase"',
        '"recent_decisions"',
    ]
    indices = [text.index(k) for k in keys_in_order]
    assert indices == sorted(indices), (
        f"keys not in alphabetical order — got positions {indices}"
    )


def test_decimal_serialised_as_string_not_float() -> None:
    """REQ_NF_FAS_001 — Decimal-as-string preserves precision."""
    response = canonical_json_response(_live_state())
    text = response.body.decode("utf-8")
    # The equity is a Decimal — it serialises as a quoted string.
    assert '"equity_after_tax":"12345.67"' in text
    # The integer count stays a JSON number.
    assert '"open_positions_count":3' in text


def test_canonical_response_status_code_default_200() -> None:
    response = canonical_json_response({"x": "y"})
    assert response.status_code == 200
    assert response.media_type == "application/json"


def test_canonical_response_explicit_status_code() -> None:
    response = canonical_json_response({"x": "y"}, status_code=202)
    assert response.status_code == 202


def test_canonical_error_response_shape() -> None:
    response = canonical_error_response("registry:token_invalid", status_code=401)
    assert response.status_code == 401
    assert response.body == b'{"error":"registry:token_invalid"}'
