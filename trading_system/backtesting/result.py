"""Backtest result types.

REQ refs:
- REQ_F_BCT_001 / REQ_NF_DET_001 — identical inputs produce identical
  equity curves and trade logs.
- REQ_F_BCT_006 — gross + after-tax PnL tracked separately.
- REQ_F_CFL_002 / REQ_SDS_MOD_005 — equity_excl_injections is the
  canonical performance series.
- REQ_F_RAT_004 / REQ_SDD_RAT_003 — ``rationales`` carries one
  ``TradeRationale`` per emitted ``Trade`` for the audit trail.
  Defaults to ``()`` so existing constructors keep working; when
  non-empty, ``len(rationales) == len(trades)`` is enforced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading_system.models.flow import EquityPoint
from trading_system.models.money import Money
from trading_system.models.rationale import TradeRationale
from trading_system.models.trading import Trade


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Outcome of a single backtest run.

    Fields:
    - ``trades`` — every Trade emitted during the run, in execution
      order. Caller can group by strategy via the originating Order
      (held by the engine; not duplicated here).
    - ``equity_curve`` — after-tax equity points, one per tick on which
      the engine called ``record_equity``.
    - ``equity_excl_injections`` — canonical performance series: the
      after-tax equity stripped of cumulative injections.
    - ``final_cash`` / ``final_equity_after_tax`` — terminal values for
      quick-look reporting; the curve is authoritative.
    - ``realized_gross`` / ``realized_after_tax`` — running totals at
      end of run (REQ_F_BCT_006 invariant: net = gross - 30% on gains;
      losses pass through).
    - ``dividends_gross`` / ``dividends_after_tax`` — same for dividends.
    - ``knockouts`` — number of turbo knockouts triggered.
    - ``injections_applied`` — number of injection events replayed.
    - ``rationales`` — one ``TradeRationale`` per ``Trade`` in the
      same order (REQ_F_RAT_004). Defaults to ``()`` for backwards
      compatibility with strategies that haven't opted in yet; when
      non-empty, the length must match ``trades``.
    """

    trades: tuple[Trade, ...]
    equity_curve: tuple[EquityPoint, ...]
    equity_excl_injections: tuple[Decimal, ...]
    final_cash: Money
    final_equity_after_tax: Money
    realized_gross: Money
    realized_after_tax: Money
    dividends_gross: Money
    dividends_after_tax: Money
    knockouts: int
    injections_applied: int
    rationales: tuple[TradeRationale, ...] = field(default=())

    def __post_init__(self) -> None:
        if len(self.equity_excl_injections) != len(self.equity_curve):
            raise ValueError(
                "BacktestResult.equity_excl_injections length must match equity_curve length"
            )
        if self.knockouts < 0:
            raise ValueError(f"BacktestResult.knockouts must be >= 0, got {self.knockouts}")
        if self.injections_applied < 0:
            raise ValueError(
                f"BacktestResult.injections_applied must be >= 0, got {self.injections_applied}"
            )
        if self.rationales and len(self.rationales) != len(self.trades):
            raise ValueError(
                "BacktestResult.rationales length must match trades length when non-empty "
                f"(rationales={len(self.rationales)}, trades={len(self.trades)})"
            )
