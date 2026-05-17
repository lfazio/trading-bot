"""TC_HOV_002 — ``IndexFuturePosition`` invariants + frozen guarantee.

REQ refs: REQ_F_HOV_005, REQ_SDD_HOV_004.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.institutional.hedge_overlay import (
    IndexFuturePosition,
    OverlayPositionState,
    OverlayProposal,
)


_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 8, tzinfo=UTC)


def _open(notional: str = "100000", entry: str = "4500") -> IndexFuturePosition:
    return IndexFuturePosition(
        id=1,
        benchmark="EUROSTOXX50",
        notional=Decimal(notional),
        entry_index_level=Decimal(entry),
        entry_at=_T0,
    )


def test_open_position_constructs() -> None:
    pos = _open()
    assert pos.state is OverlayPositionState.OPEN
    assert pos.exit_index_level is None
    assert pos.closed_at is None


def test_closed_position_constructs() -> None:
    pos = IndexFuturePosition(
        id=1,
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        entry_at=_T0,
        state=OverlayPositionState.CLOSED,
        exit_index_level=Decimal("4725"),
        closed_at=_T1,
    )
    assert pos.state is OverlayPositionState.CLOSED


def test_zero_entry_index_rejected() -> None:
    with pytest.raises(ValueError, match="hov:entry_index_non_positive"):
        IndexFuturePosition(
            id=1,
            benchmark="EUROSTOXX50",
            notional=Decimal("100000"),
            entry_index_level=Decimal("0"),
            entry_at=_T0,
        )


def test_open_with_exit_fields_rejected() -> None:
    with pytest.raises(ValueError, match="hov:open_with_exit_fields"):
        IndexFuturePosition(
            id=1,
            benchmark="EUROSTOXX50",
            notional=Decimal("100000"),
            entry_index_level=Decimal("4500"),
            entry_at=_T0,
            state=OverlayPositionState.OPEN,
            exit_index_level=Decimal("4700"),  # extraneous
        )


def test_closed_missing_exit_fields_rejected() -> None:
    with pytest.raises(ValueError, match="hov:closed_missing_exit_fields"):
        IndexFuturePosition(
            id=1,
            benchmark="EUROSTOXX50",
            notional=Decimal("100000"),
            entry_index_level=Decimal("4500"),
            entry_at=_T0,
            state=OverlayPositionState.CLOSED,
            # exit_index_level + closed_at omitted
        )


def test_frozen_dataclass_rejects_runtime_mutation() -> None:
    pos = _open()
    with pytest.raises((AttributeError, TypeError)):
        pos.notional = Decimal("200000")  # type: ignore[misc]


def test_overlay_proposal_constructs() -> None:
    p = OverlayProposal(
        benchmark="EUROSTOXX50",
        side="short",
        notional=Decimal("100000"),
        target_beta_delta=Decimal("1.0"),
        cadence="weekly",
    )
    assert p.side == "short"


def test_overlay_proposal_non_positive_notional_rejected() -> None:
    with pytest.raises(ValueError, match="proposal_notional_non_positive"):
        OverlayProposal(
            benchmark="EUROSTOXX50",
            side="short",
            notional=Decimal("0"),
            target_beta_delta=Decimal("1.0"),
            cadence="weekly",
        )
