"""Tests for ``trading_system.strategy_lab.generator``.

REQ refs: REQ_F_MTO_002 (step 1: propose), REQ_F_MTO_001 (bounded
research), REQ_C_CLA_001 (no structural risk increase — caller's
responsibility; the generator just produces candidates).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_system.models.identifiers import StrategyId
from trading_system.models.meta import TradeProposal
from trading_system.models.phase import AllocationBucket
from trading_system.strategy_lab.candidate import StrategyCandidate
from trading_system.strategy_lab.generator import Generator, StaticGenerator


def _ts() -> datetime:
    return datetime(2026, 5, 8, tzinfo=UTC)


class _NoopStrategy:
    """Minimal Strategy stand-in; emits no proposals."""

    id: StrategyId = StrategyId("noop")

    def evaluate(self, state) -> list[TradeProposal]:
        _ = state
        return []


def _candidate(idx: int) -> StrategyCandidate:
    return StrategyCandidate(
        id=StrategyId(f"cand-{idx}"),
        strategy_factory=_NoopStrategy,
        bucket=AllocationBucket.STOCK,
        seed=idx,
        config_hash=f"hash-{idx}",
        generated_at=_ts(),
    )


def test_protocol_satisfied() -> None:
    g = StaticGenerator(pool=())
    assert isinstance(g, Generator)


class TestStaticGenerator:
    def test_propose_returns_first_n_then_advances(self) -> None:
        pool = tuple(_candidate(i) for i in range(5))
        g = StaticGenerator(pool=pool)
        first = g.propose(2)
        second = g.propose(2)
        third = g.propose(2)  # only 1 left -> 1 returned
        assert [c.id for c in first] == [StrategyId("cand-0"), StrategyId("cand-1")]
        assert [c.id for c in second] == [StrategyId("cand-2"), StrategyId("cand-3")]
        assert [c.id for c in third] == [StrategyId("cand-4")]
        assert g.remaining == 0

    def test_propose_zero_returns_empty(self) -> None:
        g = StaticGenerator(pool=(_candidate(0),))
        assert g.propose(0) == ()
        # Cursor unchanged.
        assert g.remaining == 1

    def test_propose_negative_returns_empty(self) -> None:
        g = StaticGenerator(pool=(_candidate(0),))
        assert g.propose(-1) == ()


class TestStrategyCandidate:
    def test_empty_config_hash_rejected(self) -> None:
        with pytest.raises(ValueError, match="config_hash"):
            StrategyCandidate(
                id=StrategyId("c"),
                strategy_factory=_NoopStrategy,
                bucket=AllocationBucket.STOCK,
                seed=0,
                config_hash="",
                generated_at=_ts(),
            )

    def test_factory_called_returns_fresh_strategy(self) -> None:
        c = _candidate(7)
        s1 = c.strategy_factory()
        s2 = c.strategy_factory()
        # Two independent instances (the StaticGenerator gives the
        # factory class itself; calling builds a fresh instance).
        assert s1 is not s2
