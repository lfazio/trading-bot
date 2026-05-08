"""Tests for ``trading_system.models.meta.RotationProposal``.

Covers TC_SCT_007 (provenance fields + frozen dataclass).

REQ refs: REQ_F_SCT_007, REQ_SDD_SCT_004.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.meta import RotationProposal
from trading_system.models.phase import MarketRegime


def _proposal(policy_id: str = "rotator-v1") -> RotationProposal:
    return RotationProposal(
        source_regime=MarketRegime.BULL,
        source_weights={"tech": Decimal("0.5"), "financials": Decimal("0.5")},
        dest_weights={"tech": Decimal("0.7"), "financials": Decimal("0.3")},
        decided_at=datetime(2026, 5, 8, tzinfo=UTC),
        policy_id=policy_id,
    )


def test_provenance_fields_present() -> None:
    p = _proposal()
    assert p.source_regime is MarketRegime.BULL
    assert p.source_weights == {"tech": Decimal("0.5"), "financials": Decimal("0.5")}
    assert p.dest_weights == {"tech": Decimal("0.7"), "financials": Decimal("0.3")}
    assert p.decided_at == datetime(2026, 5, 8, tzinfo=UTC)
    assert p.policy_id == "rotator-v1"


def test_frozen_runtime_mutation_raises() -> None:
    p = _proposal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.policy_id = "other"  # type: ignore[misc]


def test_empty_policy_id_rejected() -> None:
    with pytest.raises(ValueError, match="policy_id"):
        _proposal(policy_id="")
