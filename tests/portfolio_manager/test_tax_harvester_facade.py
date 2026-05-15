"""Tests for ``trading_system.portfolio_manager.tax_harvester_facade``.

Covers TC_PMG_005 (silently drops stale; SELL proposal shape).

REQ refs: REQ_F_PMG_004, REQ_SDD_PMG_002.
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side, StopLoss
from trading_system.portfolio_manager.tax_harvester_facade import (
    HarvestablePosition,
    TaxHarvesterFacade,
)
from trading_system.tax.harvest import HarvestSuggestion


def _stock(symbol: str) -> Stock:
    return Stock(
        id=InstrumentId(f"{symbol}.AS"),
        symbol=symbol,
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin=f"{symbol}-ISIN",
        sector="tech",
        country="NL",
    )


def _held(symbol: str, position_id: str) -> HarvestablePosition:
    return HarvestablePosition(
        position_id=position_id,
        instrument=_stock(symbol),
        stop_loss=StopLoss(price=Decimal("90.0")),
    )


def _suggestion(position_id: str, loss: str) -> HarvestSuggestion:
    return HarvestSuggestion(
        position_id=position_id,
        loss_magnitude=Money(Decimal(loss), Currency.EUR),
    )


# ---------------------------------------------------------------------------
# TC_PMG_005 — silently drops stale, SELL proposal shape
# ---------------------------------------------------------------------------


def test_stale_suggestion_silently_dropped() -> None:
    """A suggestion whose position_id is not in held_positions SHALL
    be silently dropped (REQ_SDD_PMG_002 — stale-suggestion path)."""
    facade = TaxHarvesterFacade()
    suggestions = (
        _suggestion("pos-stale", "100"),
        _suggestion("pos-held", "200"),
    )
    held = {"pos-held": _held("ASML", "pos-held")}
    proposals = facade.propose(suggestions, held_positions=held)
    # Only the held position produces a proposal.
    assert len(proposals) == 1
    assert proposals[0].instrument.id == InstrumentId("ASML.AS")


def test_held_suggestion_produces_sell_proposal_with_negative_pnl() -> None:
    facade = TaxHarvesterFacade()
    suggestion = _suggestion("pos-1", "150")
    held = {"pos-1": _held("ASML", "pos-1")}
    proposals = facade.propose((suggestion,), held_positions=held)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.side is Side.SELL
    # Harvest realises a loss — expected_net_profit is negative.
    assert proposal.expected_net_profit.amount == Decimal("-150")
    assert proposal.expected_net_profit.currency is Currency.EUR


def test_proposal_uses_facade_strategy_id_sentinel() -> None:
    facade = TaxHarvesterFacade()
    suggestion = _suggestion("pos-1", "150")
    held = {"pos-1": _held("ASML", "pos-1")}
    proposals = facade.propose((suggestion,), held_positions=held)
    assert proposals[0].source_strategy == "portfolio_manager.tax_harvester"


def test_empty_suggestions_returns_empty_tuple() -> None:
    facade = TaxHarvesterFacade()
    assert facade.propose((), held_positions={}) == ()


def test_all_stale_suggestions_returns_empty_tuple() -> None:
    facade = TaxHarvesterFacade()
    proposals = facade.propose(
        (_suggestion("pos-a", "100"), _suggestion("pos-b", "200")),
        held_positions={},
    )
    assert proposals == ()
