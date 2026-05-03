"""Protocols for the strategy layer.

REQ refs:
- REQ_SDS_MOD_006 — every strategy implements ``evaluate(state) ->
  list[TradeProposal]``.
- REQ_SDD_API_001 — ``evaluate`` is read-only over ``state``;
  mutation is a defect.
- REQ_SDD_API_002 — Protocols are runtime-checkable so tests can
  assert conformance via ``isinstance``.
- REQ_SDD_API_005 — every concrete strategy exposes a stable ``id``
  unique within the registry.
- REQ_F_STR_001..004 — concrete strategies implement this Protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from decimal import Decimal

    from trading_system.models.identifiers import InstrumentId, StrategyId
    from trading_system.models.meta import TradeProposal
    from trading_system.models.money import Money
    from trading_system.models.phase import AllocationBucket
    from trading_system.models.trading import Position
    from trading_system.result import Option
    from trading_system.strategies.state import MarketState


@runtime_checkable
class PortfolioView(Protocol):
    """Read-only portfolio surface that strategies (and the risk
    engine) need.

    Concrete portfolios SHALL satisfy this Protocol; the strategy
    layer never touches mutating methods. Method semantics:

    - ``equity()`` — after-tax equity. The canonical performance
      reference; see REQ_F_PRT_001 / REQ_SDS_MOD_011.
    - ``cash()`` — uninvested cash balance.
    - ``exposure_pct(bucket)`` — current % of equity allocated to
      ``bucket``. Returns ``Decimal(0)`` for buckets with no
      positions; never negative for non-CASH buckets.
    - ``holds(instrument_id)`` — ``True`` iff a non-zero position is
      open on ``instrument_id``. Used by low-turnover heuristics
      (REQ_C_BHV_002).
    - ``position_for(instrument_id)`` — ``Some(Position)`` when held,
      ``Nothing`` otherwise.
    """

    def equity(self) -> Money: ...

    def cash(self) -> Money: ...

    def exposure_pct(self, bucket: AllocationBucket) -> Decimal: ...

    def holds(self, instrument_id: InstrumentId) -> bool: ...

    def position_for(self, instrument_id: InstrumentId) -> Option[Position]: ...


@runtime_checkable
class Strategy(Protocol):
    """A trade-proposal producer.

    ``id`` is unique within the registry (REQ_SDD_API_005). The
    ``evaluate`` method is pure with respect to ``state`` — it MAY
    query the market data provider on the state but MUST NOT mutate
    portfolio, constraints, or screener ranking (REQ_SDD_API_001).
    """

    id: StrategyId

    def evaluate(self, state: MarketState) -> list[TradeProposal]: ...
