"""``RegimeSectorBias`` — ``MarketRegime -> dict[sector, weight]``.

Frozen / runtime-immutable (REQ_SDS_INT_004). Each regime row's
weights MUST sum to ``1.0 +/- 1e-9`` per REQ_SDD_SCT_001; the
constructor enforces this so a misconfigured table fails fast
rather than producing silently-mis-weighted proposals.

REQ refs: REQ_F_SCT_002, REQ_SDD_SCT_001.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.models.phase import MarketRegime

_WEIGHT_TOLERANCE = Decimal("1e-9")


@dataclass(frozen=True, slots=True)
class RegimeSectorBias:
    """Frozen regime-to-sector-weight table.

    The dict-of-dicts is stored as-is; the constructor copies it
    into a fresh frozen-ish view via ``MappingProxyType`` — see
    ``frozen_table`` accessor — so callers cannot mutate the
    underlying dict. The dataclass is frozen for ``__setattr__``
    discipline; the per-regime dicts are not, but the API contract
    (REQ_SDS_INT_004) forbids mutation.
    """

    table: dict[MarketRegime, dict[str, Decimal]]

    def __post_init__(self) -> None:
        if not self.table:
            raise ValueError("RegimeSectorBias.table must be non-empty")
        for regime, weights in self.table.items():
            if not weights:
                raise ValueError(f"RegimeSectorBias.table[{regime.value}] must be non-empty")
            for sector, w in weights.items():
                if not sector:
                    raise ValueError(f"RegimeSectorBias.table[{regime.value}] has empty sector key")
                if w < 0:
                    raise ValueError(
                        f"RegimeSectorBias.table[{regime.value}][{sector}] must be >= 0, got {w}"
                    )
            total = sum(weights.values(), start=Decimal(0))
            if abs(total - Decimal(1)) > _WEIGHT_TOLERANCE:
                raise ValueError(
                    f"RegimeSectorBias.table[{regime.value}] weights must sum to "
                    f"1.0 +/- 1e-9, got {total}"
                )

    def weights_for(self, regime: MarketRegime) -> dict[str, Decimal] | None:
        """Return a defensive copy of the regime's weight map, or
        ``None`` if the regime is not in the table."""
        weights = self.table.get(regime)
        if weights is None:
            return None
        return dict(weights)
