"""Tests for ``JsonResponse`` + canonical response dataclasses
(REQ_NF_WEB_002)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.phase import Phase
from trading_system.models.safety import KillSwitchState
from trading_system.webui.schemas import (
    DecisionLine,
    JsonResponse,
    LiveStateResponse,
    PromoteResponse,
    canonical_response,
)


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# JsonResponse envelope
# ---------------------------------------------------------------------------


def test_json_response_happy_path() -> None:
    r = JsonResponse(status_code=200, body='{"a":1}')
    assert r.status_code == 200
    assert r.content_type == "application/json"


def test_json_response_rejects_out_of_range_status() -> None:
    with pytest.raises(ValueError, match="status_code"):
        JsonResponse(status_code=99, body="{}")
    with pytest.raises(ValueError, match="status_code"):
        JsonResponse(status_code=600, body="{}")


def test_from_canonical_dict() -> None:
    r = JsonResponse.from_canonical({"a": 1, "b": "x"})
    obj = json.loads(r.body)
    assert obj == {"a": 1, "b": "x"}


def test_from_canonical_sorts_keys() -> None:
    """REQ_NF_WEB_002 — sorted keys for byte-identical replay."""
    a = JsonResponse.from_canonical({"z": 1, "a": 2, "m": 3})
    b = JsonResponse.from_canonical({"a": 2, "m": 3, "z": 1})
    assert a.body == b.body


def test_error_envelope() -> None:
    r = JsonResponse.error(401, "registry:token_invalid")
    assert r.status_code == 401
    obj = json.loads(r.body)
    assert obj == {"error": "registry:token_invalid"}


def test_canonical_response_helper() -> None:
    r = canonical_response({"hello": "world"})
    assert r.status_code == 200
    assert json.loads(r.body) == {"hello": "world"}


def test_json_response_is_frozen() -> None:
    r = JsonResponse(status_code=200, body="{}")
    with pytest.raises(Exception):
        r.status_code = 201  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LiveStateResponse
# ---------------------------------------------------------------------------


def _live_state(**overrides: object) -> LiveStateResponse:
    base = dict(
        account_id=AccountId("alpha"),
        as_of=_NOW,
        ks_state=KillSwitchState.ACTIVE,
        phase=Phase.TWO,
        open_positions_count=3,
        equity_after_tax=Decimal("12345.67"),
    )
    base.update(overrides)
    return LiveStateResponse(**base)  # type: ignore[arg-type]


def test_live_state_happy_path() -> None:
    s = _live_state()
    assert s.account_id == AccountId("alpha")
    assert s.phase is Phase.TWO


def test_live_state_rejects_empty_account_id() -> None:
    with pytest.raises(ValueError, match="account_id"):
        _live_state(account_id=AccountId(""))


def test_live_state_rejects_negative_positions_count() -> None:
    with pytest.raises(ValueError, match="open_positions_count"):
        _live_state(open_positions_count=-1)


def test_live_state_canonical_form_byte_identical() -> None:
    """REQ_NF_WEB_002 — identical inputs produce identical bytes."""
    a = JsonResponse.from_canonical(_live_state())
    b = JsonResponse.from_canonical(_live_state())
    assert a.body == b.body


def test_live_state_with_recent_decisions() -> None:
    s = _live_state(
        recent_decisions=(
            DecisionLine(
                at=_NOW, instrument="ASML.AS", action="BUY", reason="signal:y>4.5"
            ),
        )
    )
    r = JsonResponse.from_canonical(s)
    obj = json.loads(r.body)
    assert len(obj["recent_decisions"]) == 1
    assert obj["recent_decisions"][0]["instrument"] == "ASML.AS"


def test_live_state_decimal_preserves_precision() -> None:
    s = _live_state(equity_after_tax=Decimal("12345.67891234567"))
    obj = json.loads(JsonResponse.from_canonical(s).body)
    assert obj["equity_after_tax"] == "12345.67891234567"


# ---------------------------------------------------------------------------
# PromoteResponse
# ---------------------------------------------------------------------------


def test_promote_response_happy_path() -> None:
    p = PromoteResponse(
        promoted=True,
        strategy_id=StrategyId("core_v3"),
        account_id=AccountId("alpha"),
    )
    obj = json.loads(JsonResponse.from_canonical(p).body)
    assert obj == {
        "account_id": "alpha",
        "promoted": True,
        "strategy_id": "core_v3",
    }


def test_promote_response_rejects_empty_strategy_id() -> None:
    with pytest.raises(ValueError, match="strategy_id"):
        PromoteResponse(
            promoted=True,
            strategy_id=StrategyId(""),
            account_id=AccountId("alpha"),
        )


def test_promote_response_rejects_empty_account_id() -> None:
    with pytest.raises(ValueError, match="account_id"):
        PromoteResponse(
            promoted=True,
            strategy_id=StrategyId("core_v3"),
            account_id=AccountId(""),
        )
