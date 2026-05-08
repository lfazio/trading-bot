"""Generator Protocol + a deterministic ``StaticGenerator`` for tests.

Per REQ_SDS_MOD_014 the runtime SHALL NOT import this module.
Generators are operator-driven research tools: a real generator might
ask Claude to propose variants subject to REQ_C_CLA_001 (no
structural risk increase); the static one in this file accepts a
pre-built candidate tuple and returns it slice-by-slice — useful for
deterministic tests of the loop controller.

REQ refs: REQ_F_MTO_001 (bounded research engine; not autonomous
trading), REQ_F_MTO_002 (step 1: propose candidates), REQ_C_CLA_001,
REQ_C_CLA_002, REQ_SDS_MOD_014.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from trading_system.strategy_lab.candidate import StrategyCandidate


@runtime_checkable
class Generator(Protocol):
    """Proposes ``N`` strategy candidates per cycle."""

    def propose(self, n: int) -> tuple[StrategyCandidate, ...]: ...


@dataclass(slots=True)
class StaticGenerator:
    """Deterministic generator backed by a pre-built tuple.

    Each ``propose(n)`` call returns the next ``n`` candidates from
    the pool, without replacement. Once the pool is exhausted,
    subsequent calls return ``()``. The pool's ordering is stable so
    integration tests are bit-identical across runs.
    """

    pool: tuple[StrategyCandidate, ...]
    _cursor: int = 0

    def propose(self, n: int) -> tuple[StrategyCandidate, ...]:
        if n <= 0:
            return ()
        end = min(self._cursor + n, len(self.pool))
        out = self.pool[self._cursor : end]
        self._cursor = end
        return out

    @property
    def remaining(self) -> int:
        return max(0, len(self.pool) - self._cursor)
