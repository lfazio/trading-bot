"""Tests for ``trading_system.wealth_ops.sector_rotator.regime_sector_bias``.

Covers TC_SCT_001 (regime weights sum to 1 ± 1e-9).

REQ refs: REQ_F_SCT_002, REQ_SDD_SCT_001.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.models.phase import MarketRegime
from trading_system.wealth_ops.sector_rotator.regime_sector_bias import (
    RegimeSectorBias,
)


def _table(**rows: dict[str, str]) -> dict[MarketRegime, dict[str, Decimal]]:
    out: dict[MarketRegime, dict[str, Decimal]] = {}
    for regime_name, weights in rows.items():
        regime = MarketRegime(regime_name)
        out[regime] = {sector: Decimal(w) for sector, w in weights.items()}
    return out


class TestConstruction:
    def test_empty_table_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            RegimeSectorBias(table={})

    def test_empty_regime_row_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            RegimeSectorBias(table={MarketRegime.BULL: {}})

    def test_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match=r"must sum to 1\.0"):
            RegimeSectorBias(
                table=_table(bull={"tech": "0.6", "financials": "0.3"})  # 0.9 != 1.0
            )

    def test_weights_at_boundary_pass(self) -> None:
        # Exactly 1.0 — passes.
        RegimeSectorBias(table=_table(bull={"tech": "0.5", "financials": "0.5"}))
        # 1.0 + tolerance — passes.
        RegimeSectorBias(
            table=_table(bull={"tech": "0.5", "financials": "0.5", "extra": "0.0000000001"})
        )

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be >= 0"):
            RegimeSectorBias(table=_table(bull={"tech": "1.1", "financials": "-0.1"}))

    def test_empty_sector_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty sector key"):
            RegimeSectorBias(table=_table(bull={"": "1.0"}))


class TestWeightsFor:
    def test_known_regime_returns_defensive_copy(self) -> None:
        bias = RegimeSectorBias(table=_table(bull={"tech": "0.6", "financials": "0.4"}))
        weights = bias.weights_for(MarketRegime.BULL)
        assert weights == {"tech": Decimal("0.6"), "financials": Decimal("0.4")}
        # Mutating the returned copy must not touch the table.
        weights["tech"] = Decimal("0.99")
        assert bias.weights_for(MarketRegime.BULL)["tech"] == Decimal("0.6")

    def test_unknown_regime_returns_none(self) -> None:
        bias = RegimeSectorBias(table=_table(bull={"tech": "1.0"}))
        assert bias.weights_for(MarketRegime.BEAR) is None
