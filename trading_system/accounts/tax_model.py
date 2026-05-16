"""``TaxModel`` Protocol + ``FranceCTOTaxModel`` default
implementation (REQ_F_ACC_005 / REQ_SDS_ACC_003 / REQ_SDD_ACC_003).

The Protocol lets multiple tax models register per-account in
Phase 6 (e.g., PEA, foreign tax-holiday) without amending the
``tax/`` engine's existing surface. France CTO stays the canonical
default; REQ_C_TAX_001 holds.

Pure functions — no portfolio reference, no clock access; runtime
swaps of the model are forbidden (the per-account binding is loaded
once at startup; REQ_SDS_ACC_003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.models.money import Money
from trading_system.result import Ok, Result


@dataclass(frozen=True, slots=True)
class PositionMeta:
    """Minimum context the tax model needs about a position to apply
    realised gains / dividends. Real positions carry far more state;
    the model only needs the basis + currency so PEA-style models
    (which gate by holding period / instrument-class) have the hooks
    they need in Phase 6."""

    holding_period_days: int
    instrument_class: str    # InstrumentClass.value; keeps the model
                              # decoupled from the instrument hierarchy


@runtime_checkable
class TaxModel(Protocol):
    """Per-account tax engine. Implementations SHALL be pure — no
    portfolio reference, no clock access, no runtime mutation. The
    per-account binding is loaded once at startup (REQ_SDS_ACC_003 /
    REQ_SDS_INT_004); admitting a new model means registering a new
    implementation under this Protocol, not amending ``tax/``."""

    def apply_realized(
        self, gain: Money, position_meta: PositionMeta
    ) -> Result[Money, str]:
        """Convert a realised gross gain into its after-tax amount.

        Losses SHALL pass through pre-tax (``gain.amount <= 0`` ⇒
        ``Ok(gain)`` unchanged) — REQ_C_TAX_001 / REQ_F_TAX_001.
        """
        ...

    def apply_dividend(
        self, amount: Money, position_meta: PositionMeta
    ) -> Result[Money, str]:
        """Convert a gross dividend into its after-tax amount."""
        ...


@dataclass(frozen=True, slots=True)
class FranceCTOTaxModel:
    """France CTO / PFU — 30 % flat on realised gains and dividends.

    Default model; REQ_C_TAX_001 holds. Losses pass through pre-tax.
    """

    rate: Decimal = field(default=Decimal("0.30"))

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.rate <= Decimal(1)):
            raise ValueError(
                f"FranceCTOTaxModel.rate must lie in [0, 1], got {self.rate}"
            )

    def apply_realized(
        self, gain: Money, position_meta: PositionMeta
    ) -> Result[Money, str]:
        if gain.amount <= 0:
            # Losses pass through pre-tax (REQ_C_TAX_001).
            return Ok(gain)
        net = gain * (Decimal(1) - self.rate)
        return Ok(net)

    def apply_dividend(
        self, amount: Money, position_meta: PositionMeta
    ) -> Result[Money, str]:
        if amount.amount <= 0:
            return Ok(amount)
        net = amount * (Decimal(1) - self.rate)
        return Ok(net)
