"""Anomaly detection helpers — pure functions over equity / signal
series. The state manager's monitor calls these and forwards
``KillSwitchTrigger`` instances on breach.

REQ refs: REQ_S_KS_003 (financial triggers — drawdown, single-day
loss, rapid decline), REQ_SDD_ALG_006 (single-day-loss threshold
default 5 %), REQ_SDD_ALG_007 (rapid-decline default 10 % over 5
trading days).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from trading_system.models.flow import EquityPoint

_MIN_POINTS_FOR_DAILY_LOSS = 2


def single_day_loss_breach(curve: Sequence[EquityPoint], threshold: Decimal) -> bool:
    """REQ_SDD_ALG_006: True iff the most recent step lost more than
    ``threshold`` (a positive fraction, e.g. ``Decimal("0.05")`` =
    5 %).

    Returns ``False`` when fewer than two points are available.
    Compares after-tax equity (REQ_F_PRT_001).
    """
    if len(curve) < _MIN_POINTS_FOR_DAILY_LOSS:
        return False
    if threshold <= 0:
        return False
    prev = curve[-2].equity_after_tax.amount
    cur = curve[-1].equity_after_tax.amount
    if prev <= 0:
        return False
    loss = (prev - cur) / prev
    return loss > threshold


def rapid_decline_breach(curve: Sequence[EquityPoint], *, days: int, pct: Decimal) -> bool:
    """REQ_SDD_ALG_007: True iff the after-tax equity has fallen by
    more than ``pct`` over the last ``days`` consecutive points.

    ``days`` is the number of points to look back (1 means one prior
    point, i.e. equivalent to ``single_day_loss_breach``). Returns
    ``False`` when ``len(curve) <= days``.
    """
    if days <= 0 or pct <= 0:
        return False
    if len(curve) <= days:
        return False
    anchor = curve[-(days + 1)].equity_after_tax.amount
    cur = curve[-1].equity_after_tax.amount
    if anchor <= 0:
        return False
    decline = (anchor - cur) / anchor
    return decline > pct
