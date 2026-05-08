"""``DividendSimulator`` — credit dividends on pay date.

REQ refs:
- REQ_F_BCT_005 — dividend events credit at pay date.
- REQ_F_TAX_002 — net dividend = gross x (1 - rate).

Convention: ``Dividend.amount_gross`` is the **per-share** gross
amount. The simulator multiplies by the holder's quantity to size
the cash credit. (The SDD §6.3 pseudo-code passes ``amount_gross``
to ``net_dividend`` directly without multiplying; we adopt the
per-share interpretation here because it matches every market-data
feed and lets dividend events be reused across position sizes.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_system.data.provider import MarketDataProvider
from trading_system.models.identifiers import InstrumentId
from trading_system.models.money import Money
from trading_system.models.trading import Dividend
from trading_system.portfolio.portfolio import Portfolio
from trading_system.result import Err, Ok
from trading_system.tax.config import TaxConfig


@dataclass(slots=True)
class DividendSimulator:
    """Per-tick dividend application, idempotent on (instrument, pay_date).

    A dividend is credited at most once per (instrument_id, pay_date)
    pair; re-entering the same tick is safe.
    """

    data: MarketDataProvider
    _paid: set[tuple[InstrumentId, datetime]] = field(default_factory=set)

    def maybe_apply(
        self,
        t: datetime,
        portfolio: Portfolio,
        tax: TaxConfig,
    ) -> list[Dividend]:
        """Apply every dividend whose ``pay_date == t`` for any held
        long position."""
        applied: list[Dividend] = []
        for iid, pos in portfolio.positions().items():
            if pos.quantity <= 0:
                continue
            res = self.data.dividends(pos.instrument, t.year)
            match res:
                case Ok(divs):
                    for d in divs:
                        if d.pay_date != t:
                            continue
                        key = (iid, d.pay_date)
                        if key in self._paid:
                            continue
                        gross_total = Money(
                            d.amount_gross.amount * pos.quantity,
                            d.amount_gross.currency,
                        )
                        portfolio.apply_dividend(iid, gross_total, tax, at=t)
                        self._paid.add(key)
                        applied.append(d)
                case Err(_):
                    # No dividends recorded for this year — nothing to do.
                    continue
        return applied
