"""Tests for ``AccountRegistry.tick`` deterministic fan-out
(REQ_F_ACC_002 / REQ_SDS_ACC_002 / REQ_SDD_ACC_002).

The registry SHALL iterate accounts in ``sorted(account_id)`` order,
calling the per-account pipeline once each. Two ticks against the
same registry with the same ``(now, pipeline)`` SHALL emit the same
sequence of pipeline calls.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trading_system.accounts.account import Account
from trading_system.accounts.registry import AccountRegistry
from trading_system.accounts.tax_model import FranceCTOTaxModel
from trading_system.models.identifiers import AccountId


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


class _Sentinel:
    def __init__(self, name: str) -> None:
        self.name = name


def _account(aid: str) -> Account:
    a = AccountId(aid)
    return Account(
        id=a,
        broker=_Sentinel("broker"),
        portfolio=_Sentinel("portfolio"),
        capital_flow=_Sentinel("capital_flow"),
        phase_engine=_Sentinel("phase_engine"),
        tax_model=FranceCTOTaxModel(),
        risk_overlay=_Sentinel("risk_overlay"),
        operator_token_account_id=str(a),
    )


def test_tick_visits_every_account_once() -> None:
    registry = AccountRegistry()
    registry.add(_account("alpha")).unwrap()
    registry.add(_account("beta")).unwrap()
    registry.add(_account("gamma")).unwrap()

    visited: list[str] = []

    def pipeline(account: Account, now: datetime) -> None:
        visited.append(str(account.id))
        assert now == _NOW

    registry.tick(_NOW, pipeline)
    assert visited == ["alpha", "beta", "gamma"]


def test_tick_iterates_lex_by_id_regardless_of_insertion_order() -> None:
    """REQ_F_ACC_002 + REQ_SDS_ACC_002 — replay determinism."""
    registry = AccountRegistry()
    registry.add(_account("zulu")).unwrap()
    registry.add(_account("alpha")).unwrap()
    registry.add(_account("mike")).unwrap()

    visited: list[str] = []
    registry.tick(_NOW, lambda a, _now: visited.append(str(a.id)))
    assert visited == ["alpha", "mike", "zulu"]


def test_tick_is_replay_deterministic() -> None:
    """Two ticks against the same state produce the same call sequence."""
    registry = AccountRegistry()
    registry.add(_account("a")).unwrap()
    registry.add(_account("b")).unwrap()

    a: list[str] = []
    b: list[str] = []
    registry.tick(_NOW, lambda acct, _n: a.append(str(acct.id)))
    registry.tick(_NOW, lambda acct, _n: b.append(str(acct.id)))
    assert a == b


def test_tick_on_empty_registry_is_noop() -> None:
    """REQ_NF_ACC_001 corner — a registry with zero accounts SHALL
    not fail (the empty case is reachable during startup before the
    first add())."""
    registry = AccountRegistry()
    called = False

    def pipeline(_a: Account, _n: datetime) -> None:
        nonlocal called
        called = True

    registry.tick(_NOW, pipeline)
    assert called is False


def test_tick_single_account_calls_pipeline_once() -> None:
    """REQ_NF_ACC_001 — backwards-compat single-account path."""
    registry = AccountRegistry()
    registry.add(_account("default")).unwrap()
    calls: list[Account] = []
    registry.tick(_NOW, lambda a, _n: calls.append(a))
    assert len(calls) == 1
    assert str(calls[0].id) == "default"
