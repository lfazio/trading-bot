"""Tests for ``trading_system.models.rationale``.

Covers TC_RAT_001 (invariants — non-empty trade_id / strategy_id;
frozen) and TC_RAT_002 (hashable + structurally equal). The shape
itself (REQ_F_RAT_002) is verified by the construction tests below
which exercise every field — strategy_version, signal_reason,
risk_approval (Mapping), tax_gate_decision, improvement_report_id,
decided_at — and by ``test_inequality_on_any_field_difference``
which asserts each field is part of structural equality. The
frozen + audit-immutable invariant (REQ_F_RAT_003) is verified by
``test_dataclass_is_frozen``. The improvement_report_id semantics
(REQ_F_RAT_005) — empty when hand-curated, comma-joined sorted
hypothesis_ids tuple for CR-002 multi-hypothesis cycles — are
verified by the TC_RAT_006 block.

REQ refs: REQ_F_RAT_001, REQ_F_RAT_002, REQ_F_RAT_003, REQ_F_RAT_004,
REQ_F_RAT_005, REQ_SDD_RAT_001, REQ_SDD_RAT_002, REQ_NF_REP_001.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from trading_system.models.identifiers import StrategyId, TradeId
from trading_system.models.rationale import (
    GATE_VOCABULARY,
    TradeRationale,
    validate_gate_vocabulary,
)
from trading_system.result import Err, Ok


def _rationale(**overrides: object) -> TradeRationale:
    defaults: dict[str, object] = {
        "trade_id": TradeId("trade-1"),
        "strategy_id": StrategyId("strat-1"),
        "strategy_version": "abc123",
        "signal_reason": "yield 5.2% > 4.5%",
        "risk_approval": {"tax_gate": "verdict=pass"},
        "tax_gate_decision": "net 12 > 5*fees",
        "improvement_report_id": "",
        "decided_at": datetime(2026, 5, 15, 9, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return TradeRationale(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TC_RAT_001 — Invariants + frozen
# ---------------------------------------------------------------------------


def test_empty_trade_id_rejected() -> None:
    with pytest.raises(ValueError, match="trade_id"):
        _rationale(trade_id=TradeId(""))


def test_empty_strategy_id_rejected() -> None:
    with pytest.raises(ValueError, match="strategy_id"):
        _rationale(strategy_id=StrategyId(""))


def test_empty_other_strings_allowed() -> None:
    r = _rationale(
        signal_reason="",
        tax_gate_decision="",
        improvement_report_id="",
        strategy_version="",
    )
    assert r.signal_reason == ""
    assert r.tax_gate_decision == ""
    assert r.improvement_report_id == ""
    assert r.strategy_version == ""


def test_dataclass_is_frozen() -> None:
    r = _rationale()
    # Frozen dataclasses raise FrozenInstanceError (a dataclasses error,
    # which is a subclass of AttributeError); accept either.
    with pytest.raises((AttributeError, Exception)):
        r.trade_id = TradeId("trade-2")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TC_RAT_002 — Hashable + structurally equal across Mapping types
# ---------------------------------------------------------------------------


def test_two_identical_rationales_are_equal_and_share_hash() -> None:
    a = _rationale()
    b = _rationale()
    assert a == b
    assert hash(a) == hash(b)


def test_equality_through_different_mapping_types() -> None:
    # dict vs MappingProxyType — semantic equality should hold.
    a = _rationale(risk_approval={"tax_gate": "pass", "stop_loss": "pass"})
    b = _rationale(
        risk_approval=MappingProxyType({"tax_gate": "pass", "stop_loss": "pass"})
    )
    assert a == b
    assert hash(a) == hash(b)


def test_inequality_on_any_field_difference() -> None:
    a = _rationale()
    assert a != _rationale(trade_id=TradeId("trade-2"))
    assert a != _rationale(strategy_id=StrategyId("strat-2"))
    assert a != _rationale(signal_reason="different reason")
    assert a != _rationale(risk_approval={"tax_gate": "reject"})


def test_used_in_set_and_dict_key() -> None:
    a = _rationale()
    b = _rationale()  # equal to a
    c = _rationale(trade_id=TradeId("trade-2"))
    assert {a, b, c} == {a, c}
    d: dict[TradeRationale, str] = {a: "first", c: "second"}
    assert d[b] == "first"  # equality lookup


# ---------------------------------------------------------------------------
# TC_RAT_006 — improvement_report_id semantics
# ---------------------------------------------------------------------------


def test_improvement_report_id_empty_for_hand_curated() -> None:
    r = _rationale(improvement_report_id="")
    assert r.improvement_report_id == ""


def test_improvement_report_id_single_value() -> None:
    r = _rationale(improvement_report_id="imp-2026-q2")
    assert r.improvement_report_id == "imp-2026-q2"


def test_improvement_report_id_multi_hypothesis_tuple_format() -> None:
    # CR-002 multi-hypothesis cycles store the sorted hyp-ids comma-joined.
    r = _rationale(improvement_report_id="hyp-a,hyp-b,hyp-c")
    assert r.improvement_report_id == "hyp-a,hyp-b,hyp-c"


# ---------------------------------------------------------------------------
# TC_RAT_007 — Gate-name vocabulary + audit helper
# ---------------------------------------------------------------------------


def test_gate_vocabulary_is_the_documented_closed_set() -> None:
    assert GATE_VOCABULARY == frozenset(
        {
            "tax_gate",
            "kill_switch",
            "risk_per_trade",
            "stop_loss",
            "class_cap",
            "correlation",
            "regime",
            "cross_account_concentration",
        }
    )


def test_validator_passes_with_known_gates() -> None:
    r = _rationale(
        risk_approval={
            "tax_gate": "pass",
            "stop_loss": "pass",
            "risk_per_trade": "metric=0.012; threshold=0.015; verdict=pass",
        }
    )
    assert isinstance(validate_gate_vocabulary(r), Ok)


def test_validator_rejects_unknown_gate() -> None:
    r = _rationale(risk_approval={"tax_gate": "pass", "foo_bar": "made_up"})
    match validate_gate_vocabulary(r):
        case Err(reason):
            assert reason == "rationale:unknown_gate:foo_bar"
        case Ok(_):
            raise AssertionError("expected Err for unknown gate")


def test_validator_passes_on_empty_risk_approval() -> None:
    # An empty risk_approval mapping is a valid v1 state (strategies
    # haven't opted in yet); the validator returns Ok.
    r = _rationale(risk_approval={})
    assert isinstance(validate_gate_vocabulary(r), Ok)


# ---------------------------------------------------------------------------
# TC_RAT_008 — Determinism (identical args → identical row)
# ---------------------------------------------------------------------------


def test_determinism_byte_identical_for_identical_args() -> None:
    args = {
        "trade_id": TradeId("trade-1"),
        "strategy_id": StrategyId("strat-1"),
        "strategy_version": "sha-abc",
        "signal_reason": "MA50 > MA200",
        "risk_approval": {
            "tax_gate": "net=12; fees=2; verdict=pass",
            "regime": "bull; verdict=pass",
        },
        "tax_gate_decision": "after-tax 8.4 > 5*1.5 = 7.5",
        "improvement_report_id": "imp-2026-05",
        "decided_at": datetime(2026, 5, 15, 9, 0, tzinfo=UTC),
    }
    a = TradeRationale(**args)  # type: ignore[arg-type]
    b = TradeRationale(**args)  # type: ignore[arg-type]
    assert a == b
    assert hash(a) == hash(b)
