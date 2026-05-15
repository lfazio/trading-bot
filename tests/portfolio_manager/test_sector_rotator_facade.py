"""Tests for ``trading_system.portfolio_manager.sector_rotator_facade``.

Covers TC_PMG_004 (empty no-op + per-instrument BUY/SELL).

REQ refs: REQ_F_PMG_003, REQ_SDD_PMG_003.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.meta import RotationProposal
from trading_system.models.money import Currency
from trading_system.models.phase import MarketRegime
from trading_system.models.trading import Side, StopLoss
from trading_system.portfolio_manager.sector_rotator_facade import (
    SectorRotatorFacade,
)


def _stock(symbol: str, sector: str) -> Stock:
    return Stock(
        id=InstrumentId(f"{symbol}.AS"),
        symbol=symbol,
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin=f"{symbol}-ISIN",
        sector=sector,
        country="NL",
    )


def _stop_loss() -> StopLoss:
    return StopLoss(price=Decimal("90.0"))


def _rotation(*, source: dict[str, str], dest: dict[str, str]) -> RotationProposal:
    return RotationProposal(
        source_regime=MarketRegime.BULL,
        source_weights={k: Decimal(v) for k, v in source.items()},
        dest_weights={k: Decimal(v) for k, v in dest.items()},
        decided_at=datetime(2026, 5, 15, 9, 0, tzinfo=UTC),
        policy_id="cr-010-default",
    )


# ---------------------------------------------------------------------------
# TC_PMG_004 — empty no-op + per-instrument BUY/SELL
# ---------------------------------------------------------------------------


def test_empty_rotation_proposals_returns_empty_tuple() -> None:
    """REQ_SDD_PMG_003 — phase-1..4 callers handle the no-op
    uniformly."""
    facade = SectorRotatorFacade()
    assert (
        facade.propose(
            (),
            instruments_by_sector={},
            default_stop_loss=_stop_loss(),
        )
        == ()
    )


def test_sector_target_above_source_emits_buy_proposals() -> None:
    facade = SectorRotatorFacade()
    rotation = _rotation(
        source={"tech": "0.20", "financials": "0.30"},
        dest={"tech": "0.35", "financials": "0.30"},  # tech up, financials unchanged
    )
    instruments_by_sector = {
        "tech": (_stock("ASML", "tech"), _stock("SAP", "tech")),
        "financials": (_stock("BNP", "financials"),),
    }
    proposals = facade.propose(
        (rotation,),
        instruments_by_sector=instruments_by_sector,
        default_stop_loss=_stop_loss(),
    )
    # tech rebalance → two BUY proposals (one per tech instrument).
    # financials weight unchanged → no proposal.
    assert len(proposals) == 2
    for p in proposals:
        assert p.side is Side.BUY
        assert p.instrument.id in (InstrumentId("ASML.AS"), InstrumentId("SAP.AS"))


def test_sector_target_below_source_emits_sell_proposals() -> None:
    facade = SectorRotatorFacade()
    rotation = _rotation(
        source={"tech": "0.40"},
        dest={"tech": "0.20"},  # tech down
    )
    proposals = facade.propose(
        (rotation,),
        instruments_by_sector={"tech": (_stock("ASML", "tech"),)},
        default_stop_loss=_stop_loss(),
    )
    assert len(proposals) == 1
    assert proposals[0].side is Side.SELL


def test_unchanged_sectors_skipped() -> None:
    facade = SectorRotatorFacade()
    rotation = _rotation(
        source={"tech": "0.30"},
        dest={"tech": "0.30"},  # unchanged
    )
    proposals = facade.propose(
        (rotation,),
        instruments_by_sector={"tech": (_stock("ASML", "tech"),)},
        default_stop_loss=_stop_loss(),
    )
    assert proposals == ()


def test_missing_sector_in_instruments_lookup_skipped() -> None:
    """A sector in dest_weights but missing from
    instruments_by_sector contributes zero proposals."""
    facade = SectorRotatorFacade()
    rotation = _rotation(
        source={"unknown": "0.20"},
        dest={"unknown": "0.40"},
    )
    proposals = facade.propose(
        (rotation,),
        instruments_by_sector={},  # empty lookup
        default_stop_loss=_stop_loss(),
    )
    assert proposals == ()


def test_proposals_use_facade_strategy_id_sentinel() -> None:
    facade = SectorRotatorFacade()
    rotation = _rotation(source={"tech": "0.20"}, dest={"tech": "0.30"})
    proposals = facade.propose(
        (rotation,),
        instruments_by_sector={"tech": (_stock("ASML", "tech"),)},
        default_stop_loss=_stop_loss(),
    )
    assert proposals[0].source_strategy == "portfolio_manager.sector_rotator"
