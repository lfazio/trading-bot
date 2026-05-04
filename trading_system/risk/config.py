"""``RiskConfig`` — frozen risk-engine parameters.

Sourced from ``config/risk.yaml``; defaults match the SDD pseudo-code
and SRS-implied values.

REQ refs:
- REQ_F_RSK_002 — single-asset cap (parsed; enforcement deferred).
- REQ_F_RSK_003 / REQ_SDD_ALG_008 — correlation max 0.85, 60-day window.
- REQ_F_STP_004 — structured products forbidden in high-vol / bear.
- REQ_SDS_INT_004 / REQ_SDD_API_004 — frozen Config; immutable at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading_system.models.instrument import InstrumentClass
from trading_system.models.phase import MarketRegime


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Risk-engine parameters."""

    # REQ_F_RSK_002 — currently parsed but enforcement deferred. The
    # field is included so config validation succeeds and a follow-up
    # step lands the gate without re-touching the YAML schema.
    single_asset_cap: Decimal = Decimal("0.30")

    # REQ_F_RSK_003 / REQ_SDD_ALG_008
    correlation_max: Decimal = Decimal("0.85")
    correlation_window_days: int = 60

    # REQ_F_STP_004 / REQ_F_RSK_003 — per InstrumentClass forbidden
    # regimes. Default mirrors config/risk.yaml.
    forbidden_regimes_for: dict[InstrumentClass, tuple[MarketRegime, ...]] = field(
        default_factory=lambda: {
            InstrumentClass.STRUCTURED: (MarketRegime.HIGH_VOL, MarketRegime.BEAR),
            InstrumentClass.TURBO: (MarketRegime.HIGH_VOL,),
        }
    )

    def __post_init__(self) -> None:
        if not (Decimal(0) < self.single_asset_cap <= Decimal(1)):
            raise ValueError(
                f"RiskConfig.single_asset_cap must lie in (0, 1], got {self.single_asset_cap}"
            )
        if not (Decimal(0) <= self.correlation_max <= Decimal(1)):
            raise ValueError(
                f"RiskConfig.correlation_max must lie in [0, 1], got {self.correlation_max}"
            )
        if self.correlation_window_days <= 0:
            raise ValueError(
                f"RiskConfig.correlation_window_days must be > 0, "
                f"got {self.correlation_window_days}"
            )
        for cls, regimes in self.forbidden_regimes_for.items():
            if not isinstance(cls, InstrumentClass):
                raise ValueError(
                    f"RiskConfig.forbidden_regimes_for keys must be "
                    f"InstrumentClass, got {type(cls).__name__}"
                )
            for r in regimes:
                if not isinstance(r, MarketRegime):
                    raise ValueError(
                        f"RiskConfig.forbidden_regimes_for[{cls}] entries "
                        f"must be MarketRegime, got {type(r).__name__}"
                    )

    def regimes_forbidden_for(self, cls: InstrumentClass) -> tuple[MarketRegime, ...]:
        """Convenience accessor — returns ``()`` when no rule for the
        class is configured."""
        return self.forbidden_regimes_for.get(cls, ())
