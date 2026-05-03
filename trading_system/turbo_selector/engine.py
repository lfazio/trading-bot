"""Turbo selector pipeline: filter -> score -> select.

Caller assembles a list of ``TurboCandidate`` (a turbo + its resolved
underlying ``Instrument``); the selector queries market data via the
``MarketDataProvider`` Protocol, applies the filter rules, scores the
survivors, and returns the best one above the configured threshold.

Phase gating: when ``PhaseConstraints.turbo_exposure_max`` is zero,
the selector returns ``Nothing()`` immediately — turbos disabled
(REQ_F_CAP_006).

REQ refs:
- REQ_F_TRB_001..006 — the full pipeline contract.
- REQ_F_CAP_006 — Phase 1 turbos disabled.
- REQ_SDD_ALG_011 — sigmoid knockout-distance score.
- REQ_SDD_CFG_004 — default scoring weights.
- REQ_SDD_API_002 — runtime-checkable Protocols (data provider).
- REQ_SDD_ERR_002 — categorized errors when data is missing
  (treated here as "skip candidate", not panic).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar, Timeframe
from trading_system.models.instrument import Instrument, Turbo
from trading_system.models.phase import PhaseConstraints
from trading_system.result import Err, Nothing, Ok, Option, Some
from trading_system.turbo_selector.config import TurboSelectorConfig
from trading_system.turbo_selector.score import (
    cost_score,
    expected_move_capture_score,
    knockout_distance_score,
    leverage_efficiency_score,
)
from trading_system.turbo_selector.stats import avg_volume, realized_vol

# How many calendar days to ask the data provider for, at minimum.
# Must cover both vol and volume windows with margin.
_LOOKBACK_PADDING = 10


@dataclass(frozen=True, slots=True)
class TurboCandidate:
    """A turbo paired with its resolved underlying ``Instrument``.

    Caller is responsible for assembling these — typically by joining
    the turbo universe against the broker's instrument book.
    """

    turbo: Turbo
    underlying: Instrument


@dataclass(frozen=True, slots=True)
class TurboScore:
    """Per-component score breakdown plus the weighted total."""

    knockout_distance: Decimal
    leverage_efficiency: Decimal
    cost: Decimal
    expected_move_capture: Decimal
    total: Decimal

    def __post_init__(self) -> None:
        for label, v in (
            ("knockout_distance", self.knockout_distance),
            ("leverage_efficiency", self.leverage_efficiency),
            ("cost", self.cost),
            ("expected_move_capture", self.expected_move_capture),
            ("total", self.total),
        ):
            if not (Decimal(0) <= v <= Decimal(1)):
                raise ValueError(f"TurboScore.{label} must lie in [0, 1], got {v}")


@dataclass(frozen=True, slots=True)
class ScoredTurbo:
    candidate: TurboCandidate
    score: TurboScore


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


def _phase_max_leverage(pc: PhaseConstraints) -> Decimal:
    """Per-phase leverage cap: SDD §4.6 maps the exposure fraction to
    a leverage multiple via ``pc.turbo_exposure_max * 100``. With
    Phase 1 turbo_exposure_max = 0, the cap is 0 and all turbos are
    rejected."""
    return pc.turbo_exposure_max * Decimal(100)


def _filter_reject(  # noqa: PLR0913 - one parameter per filter input is clearer than packing
    candidate: TurboCandidate,
    underlying_price: Decimal,
    underlying_vol: Decimal,
    underlying_volume: Decimal,
    pc: PhaseConstraints,
    cfg: TurboSelectorConfig,
) -> bool:
    """Apply the REQ_F_TRB_002 cutoffs. Returns ``True`` to reject."""
    turbo = candidate.turbo
    if underlying_price <= 0:
        return True
    knockout_distance = abs(underlying_price - turbo.knockout) / underlying_price
    if knockout_distance < cfg.knockout_min_distance:
        return True
    if turbo.spread_pct > cfg.spread_max:
        return True
    if turbo.leverage > _phase_max_leverage(pc):
        return True
    if underlying_volume < cfg.min_liquidity:
        return True
    return underlying_vol > cfg.max_volatility


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------


def _score(
    candidate: TurboCandidate,
    underlying_price: Decimal,
    underlying_vol: Decimal,
    cfg: TurboSelectorConfig,
) -> TurboScore:
    ko = knockout_distance_score(candidate.turbo, underlying_price, cfg)
    le = leverage_efficiency_score(candidate.turbo, cfg)
    co = cost_score(candidate.turbo, cfg)
    mv = expected_move_capture_score(candidate.turbo, underlying_vol, cfg)
    w = cfg.weights
    total = min(w[0] * ko + w[1] * le + w[2] * co + w[3] * mv, Decimal(1))
    return TurboScore(
        knockout_distance=ko,
        leverage_efficiency=le,
        cost=co,
        expected_move_capture=mv,
        total=total,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def select(
    candidates: Sequence[TurboCandidate],
    data: MarketDataProvider,
    pc: PhaseConstraints,
    cfg: TurboSelectorConfig,
    *,
    at: datetime,
) -> Option[ScoredTurbo]:
    """Return the highest-scoring eligible candidate, or ``Nothing()``
    when:

    - ``pc.turbo_exposure_max == 0`` (turbos disabled — REQ_F_CAP_006);
    - no candidate survives the filter;
    - the best candidate's score is strictly below ``cfg.threshold``
      (REQ_F_TRB_004).

    Candidates whose underlying market data is unavailable
    (``Err`` from the provider) are silently dropped — same policy
    as the screener (REQ_SDD_ALG_022-style).
    """
    if pc.turbo_exposure_max <= 0:
        return Nothing()

    best: ScoredTurbo | None = None
    for candidate in candidates:
        ctx = _fetch_context(candidate.underlying, data, cfg, at=at)
        if ctx is None:
            continue
        price, vol, volume = ctx
        if _filter_reject(candidate, price, vol, volume, pc, cfg):
            continue
        score = _score(candidate, price, vol, cfg)
        if best is None or score.total > best.score.total:
            best = ScoredTurbo(candidate=candidate, score=score)

    if best is None or best.score.total < cfg.threshold:
        return Nothing()
    return Some(best)


def _fetch_context(
    underlying: Instrument,
    data: MarketDataProvider,
    cfg: TurboSelectorConfig,
    *,
    at: datetime,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Pull (price, vol, volume) for the underlying. Returns ``None``
    when any required series is missing or too short."""
    latest = data.latest(underlying)
    match latest:
        case Err(_):
            return None
        case Ok(latest_bar):
            price = latest_bar.close

    lookback_days = max(cfg.vol_window, cfg.volume_window) + _LOOKBACK_PADDING
    bars_result = data.bars(
        underlying,
        Timeframe.D1,
        at - timedelta(days=lookback_days),
        at,
    )
    bars: list[Bar]
    match bars_result:
        case Err(_):
            return None
        case Ok(values):
            bars = values

    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    vol = realized_vol(closes, cfg.vol_window)
    volume = avg_volume(volumes, cfg.volume_window)
    if vol is None or volume is None:
        return None
    return price, vol, volume
