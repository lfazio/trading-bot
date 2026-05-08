"""``CapitalFlow`` — initial capital + injection timeline.

REQ refs:
- REQ_F_CFL_001 — track every external injection (amount + at).
- REQ_F_CFL_002 — performance series excludes injections; the
  canonical "equity_excl_injections" subtracts cumulative injections at
  each point (REQ_SDS_MOD_005).
- REQ_F_CFL_004 — backtests replay an explicit injection timeline.
- REQ_SDD_ALG_017 — injections kept sorted ascending by ``.at``;
  out-of-order ``observe`` re-sorts the timeline rather than
  silently corrupting it.

The ledger is single-currency: every injection MUST share the
``initial.currency``. Multi-currency capital flow lands with the
phase-5 currency hedger (``wealth_ops/``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_system.models.flow import EquityPoint, Injection
from trading_system.models.money import Currency, Money


@dataclass(slots=True)
class CapitalFlow:
    """Ledger of starting capital + every external injection.

    Holds an ordered list of ``Injection`` records and exposes the
    queries the backtester, phase engine, and analytics layer need.
    """

    initial: Money
    injections: list[Injection] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.initial.amount <= 0:
            raise ValueError(f"CapitalFlow.initial must be > 0, got {self.initial.amount}")
        for inj in self.injections:
            if inj.amount.currency != self.initial.currency:
                raise ValueError(
                    "CapitalFlow.injections must share initial.currency, got "
                    f"{inj.amount.currency} vs {self.initial.currency}"
                )
        self.injections.sort(key=lambda i: i.at)

    @property
    def currency(self) -> Currency:
        return self.initial.currency

    def total_capital(self) -> Money:
        """Initial + sum of injections (REQ_F_CFL_001)."""
        total = self.initial
        for inj in self.injections:
            total = total + inj.amount
        return total

    def cumulative_injected_at(self, t: datetime) -> Money:
        """Sum of injections with ``inj.at <= t`` (REQ_F_CFL_002).

        The list is sorted; the loop short-circuits on the first
        injection past ``t``.
        """
        total = Money(Decimal(0), self.currency)
        for inj in self.injections:
            if inj.at <= t:
                total = total + inj.amount
            else:
                break
        return total

    def equity_excl_injections(self, curve: list[EquityPoint]) -> list[Decimal]:
        """Strip cumulative injections from each after-tax equity point.

        REQ_F_CFL_002 / REQ_SDS_MOD_005: the canonical performance
        series. Returns a list of ``Decimal`` amounts (currency dropped
        because every point shares ``self.currency``; ``EquityPoint``
        currency is checked here so a misconfigured curve fails fast).
        """
        out: list[Decimal] = []
        for p in curve:
            if p.equity_after_tax.currency != self.currency:
                raise ValueError(
                    "EquityPoint.equity_after_tax.currency must match "
                    f"CapitalFlow.currency, got "
                    f"{p.equity_after_tax.currency} vs {self.currency}"
                )
            inj = self.cumulative_injected_at(p.at)
            out.append(p.equity_after_tax.amount - inj.amount)
        return out

    def observe(self, tx: Injection) -> None:
        """Record an injection; re-sort the timeline (REQ_SDD_ALG_017)."""
        if tx.amount.currency != self.currency:
            raise ValueError(
                "Injection.amount.currency must match CapitalFlow.currency, "
                f"got {tx.amount.currency} vs {self.currency}"
            )
        self.injections.append(tx)
        self.injections.sort(key=lambda i: i.at)
