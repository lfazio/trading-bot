"""``BacktestConfig`` — frozen configuration for a single backtest run.

REQ refs:
- REQ_SDS_ARC_005 — seed is part of Config so registry entries capture it.
- REQ_F_BCT_001 / REQ_NF_DET_001 — same (seed, config, data) tuple
  produces identical equity curves and trade logs.
- REQ_F_BCT_007 — explicit injection schedule replay.
- REQ_SDD_ALG_019 — tick ordering deterministic by
  (timestamp, instrument_id, sequence_id).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_system.data.types import Timeframe
from trading_system.models.flow import Injection
from trading_system.models.money import Money
from trading_system.tax.config import TaxConfig


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """All knobs the backtest engine reads at startup.

    The fee and slippage models are wired in separately at engine
    construction (``Backtest.__init__``) since they carry runtime
    behavior; ``BacktestConfig`` only carries serializable settings.
    """

    seed: int
    start: datetime
    end: datetime
    timeframe: Timeframe
    starting_capital: Money
    tax: TaxConfig
    injection_schedule: tuple[Injection, ...] = field(default_factory=tuple)
    spread_pct: Decimal = field(default_factory=lambda: Decimal(0))

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(f"BacktestConfig.start ({self.start}) must be < end ({self.end})")
        if self.starting_capital.amount <= 0:
            raise ValueError(
                f"BacktestConfig.starting_capital must be > 0, got {self.starting_capital.amount}"
            )
        if self.spread_pct < 0:
            raise ValueError(f"BacktestConfig.spread_pct must be >= 0, got {self.spread_pct}")
        for inj in self.injection_schedule:
            if inj.amount.currency != self.starting_capital.currency:
                raise ValueError(
                    "BacktestConfig.injection_schedule currency must match "
                    "starting_capital.currency"
                )
            if not (self.start <= inj.at <= self.end):
                raise ValueError(
                    f"BacktestConfig.injection_schedule: injection at {inj.at} "
                    f"outside [start={self.start}, end={self.end}]"
                )
