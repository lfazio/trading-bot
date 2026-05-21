"""Phase, regime, and per-phase constraints.

REQ refs:
- REQ_F_CAP_003 / REQ_SDD_DAT_007 — six phases, integer values 1..6.
- REQ_F_CAP_004 / REQ_F_CAP_006..011 — per-phase constraints
  (positions, trades/month, allocation, turbo cap, risk band, max DD,
  vol cap for phase 5+).
- REQ_F_CAP_012 — portfolio vol cap is non-None only for phase 5+.
- REQ_SDD_ALG_020 — allocation_targets sum to 1.0 ± 1e-9.
- REQ_SDD_TYP_003 — enums as ``IntEnum`` / ``StrEnum``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import IntEnum, StrEnum

ALLOCATION_TOLERANCE = Decimal("1e-9")


class AllocationBucket(StrEnum):
    """Phase-level allocation buckets (REQ_F_CAP_006..011, SDD §4.2 table).

    These are *strategy-allocation* buckets, not instrument classes:
    ``STOCK`` and ``TACTICAL`` both hold equity instruments
    (``InstrumentClass.STOCK``) but are budgeted separately so the core
    long-term strategy and the tactical strategy each get an explicit
    capital share. ``CASH`` may be negative when leveraged turbo
    exposure pushes total invested capital above 100 %.
    """

    STOCK = "stock"
    TACTICAL = "tactical"
    STRUCTURED = "structured"
    TURBO = "turbo"
    CASH = "cash"


class Phase(IntEnum):
    """Six capital phases. Higher phases unlock more capacity but
    impose tighter risk caps (REQ_F_CAP_003).

    1 = Capital Builder, 2 = Stability, 3 = Systematic,
    4 = Capital Acceleration, 5 = Wealth Preservation, 6 = Scale.
    """

    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6


class MarketRegime(StrEnum):
    """Coarse regime label fed to risk / structured-product / strategy
    gates (REQ_F_MTO_008)."""

    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    HIGH_VOL = "high_vol"


@dataclass(frozen=True, slots=True)
class TransitionEvent:
    """A confirmed regime transition (REQ_F_RGM_005 / REQ_SDD_RGM_004).

    Lives in ``models/`` (not ``regime/``) so both the ``regime``
    detector layer and the ``persistence`` repository can import it
    without creating a package cycle (REQ_SDD_IMP_003). The
    ``regime.transition`` module re-exports the symbol for
    backwards compatibility with existing imports.
    """

    from_regime: MarketRegime
    to_regime: MarketRegime
    at: datetime
    confirmation_periods: int

    def __post_init__(self) -> None:
        if self.from_regime == self.to_regime:
            raise ValueError(
                "TransitionEvent.from_regime and to_regime must differ "
                f"(got {self.from_regime})"
            )
        if self.confirmation_periods < 1:
            raise ValueError(
                "TransitionEvent.confirmation_periods must be >= 1, "
                f"got {self.confirmation_periods}"
            )


@dataclass(frozen=True, slots=True)
class PhaseConstraints:
    """All phase-dependent limits the engines need to enforce.

    ``allocation_targets`` MUST sum to ``1.0 ± 1e-9`` (REQ_SDD_ALG_020).
    ``portfolio_vol_cap`` is ``None`` for phases 1-4 and a positive
    fraction for phases 5-6 (REQ_F_CAP_012).
    """

    max_positions: int
    max_trades_per_month: int
    allocation_targets: dict[AllocationBucket, Decimal]
    turbo_exposure_max: Decimal
    risk_per_trade_band: tuple[Decimal, Decimal]
    max_drawdown: Decimal
    portfolio_vol_cap: Decimal | None = field(default=None)

    def __post_init__(self) -> None:
        if self.max_positions <= 0:
            raise ValueError(
                f"PhaseConstraints.max_positions must be > 0, got {self.max_positions}"
            )
        if self.max_trades_per_month <= 0:
            raise ValueError(
                f"PhaseConstraints.max_trades_per_month must be > 0, "
                f"got {self.max_trades_per_month}"
            )
        if not self.allocation_targets:
            raise ValueError("PhaseConstraints.allocation_targets must be non-empty")
        total = sum(self.allocation_targets.values(), start=Decimal(0))
        if abs(total - Decimal(1)) > ALLOCATION_TOLERANCE:
            raise ValueError(
                f"PhaseConstraints.allocation_targets must sum to 1.0 ± 1e-9, got {total}"
            )
        if self.turbo_exposure_max < 0:
            raise ValueError(
                f"PhaseConstraints.turbo_exposure_max must be >= 0, got {self.turbo_exposure_max}"
            )
        lo, hi = self.risk_per_trade_band
        if not (0 < lo <= hi):
            raise ValueError(
                f"PhaseConstraints.risk_per_trade_band must be (lo, hi) with "
                f"0 < lo <= hi, got ({lo}, {hi})"
            )
        if not (0 < self.max_drawdown <= 1):
            raise ValueError(
                f"PhaseConstraints.max_drawdown must lie in (0, 1], got {self.max_drawdown}"
            )
        if self.portfolio_vol_cap is not None and self.portfolio_vol_cap <= 0:
            raise ValueError(
                f"PhaseConstraints.portfolio_vol_cap must be > 0 when set, "
                f"got {self.portfolio_vol_cap}"
            )
