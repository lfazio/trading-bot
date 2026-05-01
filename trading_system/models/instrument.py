"""Instrument hierarchy.

``Instrument`` is the base type carrying the universal fields
(id, symbol, exchange, currency) plus an explicit ``InstrumentClass``
discriminator. Concrete subclasses add type-specific fields and verify
the discriminator at construction.

REQ refs: REQ_F_BRK_001, REQ_F_TRB_006 (turbo metadata), REQ_F_STP_002
(structured-product fields), REQ_SDD_TYP_002 / REQ_SDD_TYP_003.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from trading_system.models.identifiers import InstrumentId
from trading_system.models.money import Currency, Money


class InstrumentClass(StrEnum):
    """Top-level instrument category — drives allocation buckets."""

    STOCK = "stock"
    TURBO = "turbo"
    STRUCTURED = "structured"
    CASH = "cash"


@dataclass(frozen=True, slots=True)
class Instrument:
    """Base instrument metadata. Use a concrete subclass for trading."""

    id: InstrumentId
    symbol: str
    exchange: str
    currency: Currency
    cls: InstrumentClass

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Instrument.symbol must be non-empty")
        if not self.exchange:
            raise ValueError("Instrument.exchange must be non-empty")


@dataclass(frozen=True, slots=True)
class Stock(Instrument):
    """Listed equity. ``cls`` MUST be ``InstrumentClass.STOCK``."""

    isin: str
    sector: str
    country: str

    def __post_init__(self) -> None:
        Instrument.__post_init__(self)
        if self.cls is not InstrumentClass.STOCK:
            raise ValueError(f"Stock.cls must be STOCK, got {self.cls!r}")
        if not self.isin:
            raise ValueError("Stock.isin must be non-empty")


TurboDirection = Literal["LONG", "SHORT"]


@dataclass(frozen=True, slots=True)
class Turbo(Instrument):
    """Knockout-leveraged certificate (REQ_F_TRB_005, REQ_F_TRB_006)."""

    underlying: InstrumentId
    direction: TurboDirection
    leverage: Decimal
    knockout: Decimal  # absolute price level of the barrier
    spread_pct: Decimal  # bid/ask spread, fractional

    def __post_init__(self) -> None:
        Instrument.__post_init__(self)
        if self.cls is not InstrumentClass.TURBO:
            raise ValueError(f"Turbo.cls must be TURBO, got {self.cls!r}")
        if not self.underlying:
            raise ValueError("Turbo.underlying must be non-empty")
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"Turbo.direction must be LONG or SHORT, got {self.direction!r}")
        if self.leverage <= 1:
            raise ValueError(f"Turbo.leverage must be > 1, got {self.leverage}")
        if self.knockout <= 0:
            raise ValueError(f"Turbo.knockout must be > 0, got {self.knockout}")
        if self.spread_pct < 0:
            raise ValueError(f"Turbo.spread_pct must be >= 0, got {self.spread_pct}")


PayoffType = Literal["AUTOCALL", "BARRIER", "CAPITAL_PROT", "LEV_CERT"]


@dataclass(frozen=True, slots=True)
class StructuredProduct(Instrument):
    """Structured product (autocallable / barrier / capital-protected /
    leveraged certificate). Decomposition lives in the
    ``structured_products/`` engine, not here (REQ_F_STP_002).
    """

    underlying: InstrumentId
    payoff: PayoffType
    issuer: str
    barriers: tuple[Decimal, ...]
    notional: Money

    def __post_init__(self) -> None:
        Instrument.__post_init__(self)
        if self.cls is not InstrumentClass.STRUCTURED:
            raise ValueError(f"StructuredProduct.cls must be STRUCTURED, got {self.cls!r}")
        if not self.underlying:
            raise ValueError("StructuredProduct.underlying must be non-empty")
        if self.payoff not in ("AUTOCALL", "BARRIER", "CAPITAL_PROT", "LEV_CERT"):
            raise ValueError(f"StructuredProduct.payoff invalid: {self.payoff!r}")
        if not self.issuer:
            raise ValueError("StructuredProduct.issuer must be non-empty")
        if self.notional.amount <= 0:
            raise ValueError(f"StructuredProduct.notional must be > 0, got {self.notional.amount}")
