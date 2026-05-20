"""CR-004 Phase B — ``render_canonical()`` helpers on every
response dataclass under ``webui/schemas.py``
(REQ_SDD_WEB_007).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.phase import Phase
from trading_system.models.safety import KillSwitchState
from trading_system.webui.schemas import (
    DecisionLine,
    LiveStateResponse,
    PromoteResponse,
)


_AT = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)


def test_live_state_render_canonical_is_byte_identical_across_calls() -> None:
    """REQ_NF_WEB_002 family — identical inputs produce identical
    canonical-JSON bodies."""
    payload = LiveStateResponse(
        account_id=AccountId("default"),
        as_of=_AT,
        ks_state=KillSwitchState.ACTIVE,
        phase=Phase(1),
        open_positions_count=3,
        equity_after_tax=Decimal("12345.67"),
    )
    a = payload.render_canonical()
    b = payload.render_canonical()
    assert a == b


def test_live_state_render_canonical_sorted_keys_decimal_as_string() -> None:
    """REQ_SDD_WEB_007 — sorted keys; Decimal as string; ISO-8601
    datetimes with UTC offset."""
    payload = LiveStateResponse(
        account_id=AccountId("default"),
        as_of=_AT,
        ks_state=KillSwitchState.ACTIVE,
        phase=Phase(1),
        open_positions_count=3,
        equity_after_tax=Decimal("12345.67"),
    )
    text = payload.render_canonical()
    # Keys in alphabetical order.
    keys = [
        '"account_id"',
        '"as_of"',
        '"equity_after_tax"',
        '"ks_state"',
        '"open_positions_count"',
        '"phase"',
        '"recent_decisions"',
    ]
    indices = [text.index(k) for k in keys]
    assert indices == sorted(indices), (
        f"keys not alphabetical; positions = {indices}"
    )
    # Decimal serialised as a quoted string (precision preserved).
    assert '"equity_after_tax":"12345.67"' in text
    # ISO-8601 datetime with UTC offset.
    assert "2026-05-18T12:00:00+00:00" in text


def test_promote_response_render_canonical() -> None:
    payload = PromoteResponse(
        promoted=True,
        strategy_id=StrategyId("alpha-v2"),
        account_id=AccountId("default"),
    )
    text = payload.render_canonical()
    assert '"promoted":true' in text
    assert '"strategy_id":"alpha-v2"' in text
    # Two calls byte-identical.
    assert text == payload.render_canonical()


def test_decision_line_render_canonical() -> None:
    line = DecisionLine(
        at=_AT,
        instrument="ASML.AS",
        action="BUY",
        reason="yield>4.5 + payout<70",
    )
    text = line.render_canonical()
    assert '"action":"BUY"' in text
    assert '"instrument":"ASML.AS"' in text
    assert "2026-05-18T12:00:00+00:00" in text
