"""Tests for ``trading_system.wealth_ops.sector_rotator.taxonomy``.

REQ refs: REQ_F_SCT_005, REQ_SDD_SCT_003.
"""

from __future__ import annotations

import pytest

from trading_system.result import Err, Ok
from trading_system.wealth_ops.sector_rotator.taxonomy import SectorTaxonomy


class TestConstruction:
    def test_empty_set_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            SectorTaxonomy(allowed=frozenset())

    def test_empty_string_in_set_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty strings"):
            SectorTaxonomy(allowed=frozenset({"tech", ""}))


class TestValidate:
    def test_known_sector_returns_ok(self) -> None:
        t = SectorTaxonomy(allowed=frozenset({"tech", "financials"}))
        assert t.validate("tech") == Ok(None)

    def test_unknown_sector_returns_categorised_err(self) -> None:
        t = SectorTaxonomy(allowed=frozenset({"tech"}))
        match t.validate("crypto"):
            case Err(reason):
                assert reason == "data:unknown_sector:crypto"
            case Ok(_):
                raise AssertionError("expected Err")
