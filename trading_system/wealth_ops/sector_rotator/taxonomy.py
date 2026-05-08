"""``SectorTaxonomy`` — operator-supplied canonical sector vocabulary.

Loaded at startup; runtime-immutable per REQ_SDS_INT_004.
Sectors emitted by the screener that do not appear in ``allowed``
surface as ``Err("data:unknown_sector:<sector>")`` — the rotator
drops the entire rotation cycle in that branch (REQ_SDD_SCT_003) so
unknown taxonomy values cannot quietly bypass the policy.

REQ refs: REQ_F_SCT_005, REQ_SDS_SCT_001 (read-only public surface),
REQ_SDD_SCT_003 (categorised error string).
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_system.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class SectorTaxonomy:
    """Frozen set of canonical sector identifiers."""

    allowed: frozenset[str]

    def __post_init__(self) -> None:
        if not self.allowed:
            raise ValueError("SectorTaxonomy.allowed must be non-empty")
        for s in self.allowed:
            if not s:
                raise ValueError("SectorTaxonomy.allowed must not contain empty strings")

    def validate(self, sector: str) -> Result[None, str]:
        """``Ok(None)`` if ``sector`` is in the taxonomy, otherwise
        ``Err("data:unknown_sector:<sector>")`` per REQ_SDD_SCT_003."""
        if sector in self.allowed:
            return Ok(None)
        return Err(f"data:unknown_sector:{sector}")
