"""Scoring components for the turbo selector.

Each component returns a ``Decimal`` clamped to ``[0, 1]`` and depends
only on the ``Turbo`` instrument plus, where needed, the underlying's
realized volatility (already aggregated by ``stats.realized_vol``).

REQ refs:
- REQ_F_TRB_003 — composite score with weights 0.35 / 0.25 / 0.20 / 0.20.
- REQ_SDD_ALG_011 — knockout-distance is a sigmoid centred at the
  configured minimum-distance threshold.
- REQ_SDD_TYP_001 — Decimal arithmetic; no float.
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.models.instrument import Turbo
from trading_system.turbo_selector.config import TurboSelectorConfig


def _clamp01(value: Decimal) -> Decimal:
    if value < Decimal(0):
        return Decimal(0)
    if value > Decimal(1):
        return Decimal(1)
    return value


def knockout_distance_score(
    turbo: Turbo,
    underlying_price: Decimal,
    cfg: TurboSelectorConfig,
) -> Decimal:
    """Sigmoid centred at ``cfg.knockout_min_distance`` (REQ_SDD_ALG_011).

    Below the threshold the score approaches 0; above it, 1. At the
    threshold itself, the score is exactly 0.5. Steepness is set by
    ``cfg.knockout_sigmoid_k``.

    The sigmoid argument is clamped to ``[-50, 50]`` so the
    ``Decimal.exp()`` call stays in a stable range — beyond that
    the result saturates anyway.
    """
    if underlying_price <= 0:
        return Decimal(0)
    distance = abs(underlying_price - turbo.knockout) / underlying_price
    arg = -(distance - cfg.knockout_min_distance) * cfg.knockout_sigmoid_k
    if arg > Decimal(50):
        arg = Decimal(50)
    elif arg < Decimal(-50):
        arg = Decimal(-50)
    return _clamp01(Decimal(1) / (Decimal(1) + arg.exp()))


def leverage_efficiency_score(turbo: Turbo, cfg: TurboSelectorConfig) -> Decimal:
    """``min(leverage / leverage_efficiency_reference, 1)`` — higher
    leverage is more "capital-efficient" per unit of underlying
    move, up to a configured reference."""
    return _clamp01(turbo.leverage / cfg.leverage_efficiency_reference)


def cost_score(turbo: Turbo, cfg: TurboSelectorConfig) -> Decimal:
    """``1 - spread_pct / spread_max`` clamped — lower spread => better
    cost characteristics."""
    if cfg.spread_max <= 0:
        return Decimal(0)
    return _clamp01(Decimal(1) - turbo.spread_pct / cfg.spread_max)


def expected_move_capture_score(
    turbo: Turbo,
    underlying_vol: Decimal,
    cfg: TurboSelectorConfig,
) -> Decimal:
    """``min(leverage * underlying_vol / max_volatility, 1)`` — the
    fraction of an "extreme" daily-vol move captured. Pegs at 1 when
    leverage * vol reaches the volatility filter ceiling.
    """
    if cfg.max_volatility <= 0:
        return Decimal(0)
    capture = turbo.leverage * underlying_vol / cfg.max_volatility
    return _clamp01(capture)
