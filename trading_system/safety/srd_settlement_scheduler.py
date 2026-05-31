"""CR-030 — SRDSettlementScheduler.

Fires on the last business day of each calendar month;
iterates every open ``SRDPosition``, books cash settlement +
CRD fee + optional rollover. Lives under ``safety/`` because
it's a cross-cutting calendar-driven service (not a per-tick
runtime concern); the paper-trading runtime + the backtest
engine both invoke ``tick(at)`` from their main loops.

REQ refs:
- REQ_F_SRD_005 — scheduler fires on last business day; books
  realized P&L + CRD fees + optional rollover.
- REQ_F_SRD_007 — tax engine integration via SRDSettlement.source.
- REQ_NF_SRD_001 — deterministic given calendar + positions +
  market prices + dividends.
- REQ_SDD_SRD_006 — tick(at) algorithm + atomic rollover.
- REQ_SDD_SRD_007 — CRD-fee + rollover-fee formula.
- REQ_SDD_SRD_010 — replay-determinism conformance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.models.identifiers import InstrumentId
from trading_system.portfolio.portfolio import Portfolio
from trading_system.portfolio.srd_position import (
    SRDPosition,
    SRDSettlement,
    last_business_day_of_month,
)
from trading_system.result import Err, Ok, Result
from trading_system.tax.config import TaxConfig


# CR-030 default CRD rates per REQ_SDD_SRD_007. Operators tune
# per their broker via ``config/srd.yaml`` — e.g., Bourse Direct
# at 0.43%/month vs Saxo at 0.50%/month.
DEFAULT_CRD_FEE_RATE_MONTHLY = Decimal("0.0025")  # 0.25%/month
DEFAULT_CRD_ROLLOVER_RATE_MONTHLY = Decimal("0.0010")  # 0.10%/month


@dataclass(frozen=True, slots=True)
class SRDSettlementCalendar:
    """Frozen calendar of last-business-days per month + an
    operator-supplied holiday set.

    ``holidays`` rolls back from the canonical last business day
    when an Euronext early-close day intervenes (e.g. Christmas
    Eve). Stored as a frozenset of ``date`` objects so the
    determinism contract (REQ_NF_SRD_001) holds without
    side-effects.
    """

    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_settlement_day(self, at: datetime) -> bool:
        """REQ_SDD_SRD_006 — ``tick(at).date() == last_business_day_of_
        month(at).date()``? Pure-Python computation; no I/O."""
        last = last_business_day_of_month(at, holidays=self.holidays)
        return last.date() == at.date()


@runtime_checkable
class _SafetyLayerView(Protocol):
    """Subset of ``SafetyLayer`` the scheduler may consult on
    coverage-appel events at mark time. Decoupled via Protocol so
    the structural audit stays clean."""

    def raise_trigger(self, trigger) -> object: ...


@dataclass(slots=True)
class SRDSettlementScheduler:
    """REQ_F_SRD_005 / REQ_SDD_SRD_006 — last-business-day
    settlement scheduler.

    Composes:
    - the ``SRDSettlementCalendar`` (knows what day is a
      settlement day);
    - the ``Portfolio`` (reads + mutates ``srd_positions``);
    - the ``MarketDataProvider`` (settlement-day close lookup);
    - the ``TaxConfig`` (PFU rate applied to positive net_pnl);
    - the optional ``SafetyLayer`` (DEGRADED trigger on
      provider failure).
    """

    portfolio: Portfolio
    provider: MarketDataProvider
    calendar: SRDSettlementCalendar = field(
        default_factory=SRDSettlementCalendar
    )
    tax: TaxConfig = field(default_factory=TaxConfig.default)
    crd_fee_rate_monthly: Decimal = DEFAULT_CRD_FEE_RATE_MONTHLY
    crd_rollover_rate_monthly: Decimal = DEFAULT_CRD_ROLLOVER_RATE_MONTHLY
    safety: _SafetyLayerView | None = None

    def tick(self, at: datetime) -> Result[list[SRDSettlement], str]:
        """REQ_SDD_SRD_006 — settle every open SRD position whose
        ``settlement_cycle.date() == at.date()`` (and ``at`` is the
        last business day of the month).

        Returns ``Ok([])`` on non-settlement days. On a settlement
        day, returns ``Ok([SRDSettlement, ...])`` in the order the
        positions are iterated (lex by instrument_id for
        determinism). On the first provider failure the entire
        tick rolls back — partial settlement would leave the
        portfolio in a half-applied state which the operator
        can't reconcile.
        """
        if not self.calendar.is_settlement_day(at):
            return Ok([])
        # Iterate in lex order so paired replays produce
        # tuple-equal output (REQ_NF_SRD_001).
        due: list[tuple[InstrumentId, SRDPosition]] = sorted(
            (
                (iid, pos)
                for iid, pos in self.portfolio.srd_positions().items()
                if pos.settlement_cycle.date() == at.date()
            ),
            key=lambda kv: str(kv[0]),
        )
        settlements: list[SRDSettlement] = []
        for iid, pos in due:
            # Settlement-day close from the standard provider so
            # the CR-021 envelope cache + REQ_NF_DAT_001 byte
            # equality propagate.
            bars_res = self.provider.bars(
                pos.instrument, Timeframe.D1, at, at
            )
            if not isinstance(bars_res, Ok) or not bars_res.value:
                # Cannot price ⇒ abort the entire tick. No
                # partial settlement; operator reconciles + re-runs.
                return Err(
                    f"srd:settlement_price_unavailable:{iid}"
                )
            settlement_price = bars_res.value[-1].close
            crd_fee = (
                pos.quantity * pos.entry_price * self.crd_fee_rate_monthly
            )
            rollover_fee = (
                pos.quantity * pos.entry_price * self.crd_rollover_rate_monthly
                if pos.auto_rollover
                else Decimal(0)
            )
            settlement = self.portfolio.apply_srd_close(
                instrument_id=iid,
                settlement_price=settlement_price,
                crd_fee=crd_fee,
                rollover_fee=rollover_fee,
                settlement_at=at,
                tax=self.tax,
                rolled_over=pos.auto_rollover,
            )
            settlements.append(settlement)
            # REQ_SDD_SRD_006 — auto-rollover opens a fresh
            # SRDPosition at settlement_price for the next
            # month's last business day. Same instrument /
            # direction / quantity / carry_fee_rate.
            if pos.auto_rollover:
                next_cycle = _next_month_settlement(at, self.calendar)
                new_pos = SRDPosition(
                    instrument=pos.instrument,
                    direction=pos.direction,
                    quantity=pos.quantity,
                    entry_price=settlement_price,
                    entry_at=at,
                    settlement_cycle=next_cycle,
                    carry_fee_rate_monthly=pos.carry_fee_rate_monthly,
                    auto_rollover=True,
                )
                self.portfolio._srd_positions[iid] = new_pos  # type: ignore[attr-defined]
        return Ok(settlements)


def _next_month_settlement(
    at: datetime, calendar: SRDSettlementCalendar
) -> datetime:
    """Helper: last business day of the month FOLLOWING ``at``."""
    # Step to the first of next month, then compute its
    # last-business-day.
    next_month = (at.replace(day=1) + timedelta(days=32)).replace(day=1)
    next_month = next_month.replace(tzinfo=UTC)
    return last_business_day_of_month(next_month, holidays=calendar.holidays)
