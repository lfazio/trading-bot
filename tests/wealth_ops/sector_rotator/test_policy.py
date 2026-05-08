"""Tests for ``trading_system.wealth_ops.sector_rotator.policy``.

REQ refs: REQ_F_SCT_003, REQ_F_SCT_004, REQ_F_SCT_006,
REQ_SDS_SCT_003, REQ_SDD_SCT_005.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from trading_system.models.phase import MarketRegime
from trading_system.wealth_ops.sector_rotator.policy import (
    HoldingState,
    RotationPolicy,
)


class TestRotationPolicy:
    def test_defaults(self) -> None:
        p = RotationPolicy()
        assert p.min_holding_days == 60
        assert p.max_rotations_per_quarter == 1
        assert p.whipsaw_dampener == 1

    @pytest.mark.parametrize(
        "field_name", ["min_holding_days", "max_rotations_per_quarter", "whipsaw_dampener"]
    )
    def test_negative_values_rejected(self, field_name: str) -> None:
        with pytest.raises(ValueError, match=field_name):
            RotationPolicy(**{field_name: -1})

    def test_frozen(self) -> None:
        p = RotationPolicy()
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.min_holding_days = 30  # type: ignore[misc]


class TestHoldingState:
    def test_default_construction(self) -> None:
        s = HoldingState()
        assert s.last_entry == {}
        assert s.last_exit == {}
        assert s.rotations_this_quarter == 0
        assert s.quarter_started_at is None
        assert s.regime_episode is None
        assert s.direction_changes_in_episode == 0

    def test_snapshot_is_defensive_copy(self) -> None:
        s = HoldingState()
        s.last_entry["tech"] = datetime(2026, 1, 1, tzinfo=UTC)
        s.rotations_this_quarter = 1
        s.regime_episode = (MarketRegime.BULL, datetime(2026, 1, 1, tzinfo=UTC))

        snap = s.snapshot()
        assert snap.last_entry == s.last_entry
        assert snap.rotations_this_quarter == 1
        assert snap.regime_episode == s.regime_episode

        # Mutating the live state does not change the snapshot.
        s.last_entry["financials"] = datetime(2026, 2, 1, tzinfo=UTC)
        s.rotations_this_quarter = 5
        assert "financials" not in snap.last_entry
        assert snap.rotations_this_quarter == 1

        # Mutating the snapshot does not change the live state.
        snap.last_entry["healthcare"] = datetime(2026, 3, 1, tzinfo=UTC)
        assert "healthcare" not in s.last_entry
