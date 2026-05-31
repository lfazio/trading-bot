"""Trading primitives — orders, trades, positions, dividends.

REQ refs:
- REQ_F_CAP_014 — stop-loss is mandatory in every phase, encoded as a
  required, non-optional field on ``Order`` and ``Position``.
- REQ_SDD_DAT_001 — ``StopLoss`` is a required field on those types;
  constructors reject ``None``.
- REQ_SDD_DAT_002 — ``Position.opened_at`` and ``avg_price`` are tax-basis
  inputs and must be set at construction.
- REQ_SDD_DAT_005 — ``Trade.fees`` is the executed fee amount, never an
  estimate; estimates live on ``TradeProposal`` only.
- REQ_SDD_DAT_006 — ``Order.quantity`` and ``Position.quantity``
  magnitude must be strictly positive; zero or negative magnitudes raise.
- REQ_SDD_TYP_001 — Decimal arithmetic; REQ_SDD_TYP_003 — StrEnum tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import Instrument
from trading_system.models.money import Money


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    # CR-030 (REQ_F_SRD_002 / REQ_SDD_SRD_002) — SRD (Service de
    # Règlement Différé) margin variants. The instrument SHALL be
    # SRD-eligible (membership-checked at Order construction); the
    # cash exchange happens on the last business day of the entry
    # month, not on the fill. SRD_LONG buys with deferred
    # settlement; SRD_SHORT sells short with deferred settlement.
    SRD_LONG = "srd_long"
    SRD_SHORT = "srd_short"


# CR-030 (REQ_SDD_SRD_001) — frozen set of SRD-eligible instrument
# ids loaded once at process boot. Defaults to ``frozenset()`` so a
# pre-CR-030 boot path still works; populated by
# ``set_srd_eligible_instruments(...)`` from the application boot
# wiring (typically reads ``data/universes/srd-eligible.yaml`` via
# the existing UniverseLoader).
SRD_ELIGIBLE_INSTRUMENT_IDS: frozenset = frozenset()


def set_srd_eligible_instruments(ids):
    """Replace the module-level SRD eligibility set.

    Operators call this once at boot after loading the universe;
    the Order constructor reads the frozenset on every SRD order.
    The function lives at module scope (not inside a class) so it
    runs without instantiating anything.
    """
    global SRD_ELIGIBLE_INSTRUMENT_IDS
    SRD_ELIGIBLE_INSTRUMENT_IDS = frozenset(ids)


class OrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class StopLoss:
    """Mandatory stop-loss attached to every order and open position
    (REQ_F_CAP_014, REQ_SDD_DAT_001)."""

    price: Decimal
    trailing_pct: Decimal | None = None

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"StopLoss.price must be > 0, got {self.price}")
        if self.trailing_pct is not None and not (0 < self.trailing_pct < 1):
            raise ValueError(
                f"StopLoss.trailing_pct must lie in (0, 1) when set, got {self.trailing_pct}"
            )


@dataclass(frozen=True, slots=True)
class Order:
    """Open order. ``quantity`` is unsigned magnitude; ``side`` carries
    direction."""

    id: OrderId
    instrument: Instrument
    side: Side
    quantity: Decimal
    type: OrderType
    stop_loss: StopLoss
    created_at: datetime
    source_strategy: StrategyId
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"Order.quantity must be > 0, got {self.quantity}")
        if self.type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("Order.limit_price required for LIMIT orders")
        if self.type is not OrderType.LIMIT and self.limit_price is not None:
            raise ValueError(f"Order.limit_price must be None for {self.type.value} orders")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError(f"Order.limit_price must be > 0, got {self.limit_price}")
        # CR-030 (REQ_F_SRD_002 / REQ_SDD_SRD_002) — SRD eligibility.
        # The check runs AFTER the quantity/price validators so test
        # fixtures with empty universes still fail at the bound checks
        # rather than the eligibility check.
        if self.type in (OrderType.SRD_LONG, OrderType.SRD_SHORT):
            if self.instrument.id not in SRD_ELIGIBLE_INSTRUMENT_IDS:
                raise ValueError(
                    f"Order.type {self.type} requires SRD-eligible "
                    f"instrument, got {self.instrument.id}"
                )


@dataclass(frozen=True, slots=True)
class Trade:
    """Executed fill. ``fees`` is the broker-returned amount, not an
    estimate (REQ_SDD_DAT_005)."""

    id: TradeId
    order_id: OrderId
    executed_at: datetime
    price: Decimal
    quantity_filled: Decimal
    fees: Money
    slippage: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"Trade.price must be > 0, got {self.price}")
        if self.quantity_filled <= 0:
            raise ValueError(f"Trade.quantity_filled must be > 0, got {self.quantity_filled}")


@dataclass(frozen=True, slots=True)
class Position:
    """Open position. ``quantity`` is signed (positive long, negative
    short) but magnitude must be > 0 (REQ_SDD_DAT_006)."""

    instrument: Instrument
    quantity: Decimal  # positive long, negative short
    avg_price: Decimal
    opened_at: datetime
    stop_loss: StopLoss

    def __post_init__(self) -> None:
        if self.quantity == 0:
            raise ValueError("Position.quantity must be non-zero (magnitude > 0)")
        if self.avg_price <= 0:
            raise ValueError(f"Position.avg_price must be > 0, got {self.avg_price}")


@dataclass(frozen=True, slots=True)
class Dividend:
    """Cash dividend event. ``amount_gross`` is pre-tax; the tax engine
    converts to ``amount_net`` at realization (REQ_F_TAX_002,
    REQ_F_BCT_005)."""

    instrument: InstrumentId
    ex_date: datetime
    pay_date: datetime
    amount_gross: Money
    amount_net: Money | None = field(default=None)

    def __post_init__(self) -> None:
        if self.amount_gross.amount <= 0:
            raise ValueError(f"Dividend.amount_gross must be > 0, got {self.amount_gross.amount}")
        if self.pay_date < self.ex_date:
            raise ValueError(
                f"Dividend.pay_date ({self.pay_date}) must be on or after ex_date ({self.ex_date})"
            )
        if self.amount_net is not None:
            if self.amount_net.currency != self.amount_gross.currency:
                raise ValueError("Dividend.amount_net currency must match amount_gross currency")
            if self.amount_net.amount > self.amount_gross.amount:
                raise ValueError("Dividend.amount_net cannot exceed amount_gross")
