"""Slippage models — execution-noise simulation primitives.

REQ refs: REQ_F_BCT_003 (slippage simulated in backtests),
REQ_SDS_ARC_005 (seeded determinism), REQ_SDD_TST_002 (mock data
deterministic).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.models.trading import Order, Side


@runtime_checkable
class SlippageModel(Protocol):
    """Return the signed slippage (as a price delta) to apply to a fill.

    Convention: positive return ⇒ caller pays a worse price than the
    reference (BUY fills above reference, SELL fills below). Concrete
    implementations may shift sign based on ``order.side``.
    """

    def slip(self, order: Order, reference_price: Decimal, rng: random.Random) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class GaussianSlippageModel:
    """Slippage drawn from a half-normal distribution scaled by
    ``stdev_pct``. Always adverse to the caller (BUY pays up, SELL
    receives down) so backtests cannot over-perform via favorable
    slippage.
    """

    stdev_pct: Decimal

    def __post_init__(self) -> None:
        if self.stdev_pct < 0:
            raise ValueError(f"GaussianSlippageModel.stdev_pct must be >= 0, got {self.stdev_pct}")

    def slip(self, order: Order, reference_price: Decimal, rng: random.Random) -> Decimal:
        # Half-normal: |gauss(0, stdev)|. Always adverse.
        magnitude = abs(rng.gauss(0, float(self.stdev_pct)))
        delta = reference_price * Decimal(str(magnitude))
        return delta if order.side is Side.BUY else -delta


@dataclass(frozen=True, slots=True)
class ZeroSlippageModel:
    """No slippage. Useful for unit tests where determinism matters
    more than realism."""

    def slip(self, order: Order, reference_price: Decimal, rng: random.Random) -> Decimal:
        return Decimal(0)
