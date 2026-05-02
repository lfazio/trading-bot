"""Screener pipeline: filter -> score -> rank.

Pure functions over ``Stock`` and ``Fundamentals``. The data layer is
queried via the ``MarketDataProvider`` Protocol (``data.fundamentals``);
``Err`` from that provider is treated as "skip this instrument" — a
filter pass-by-omission, not a panic, since unknown fundamentals
simply mean the screener cannot vouch for the candidate.

Scoring choices
---------------
The SDD §4.4 pseudo-code mentions hypothetical inputs (dividend-growth
std-dev, P/FCF multiple) that the current ``Fundamentals`` type does
not carry. The score helpers here use the fields actually present:

- ``stability_score(f)`` — ``min(history_years / full_years, 1)``;
  ``full_years`` defaults to 20. A 20+ year track record is "fully
  stable"; below that the score grows linearly with history.
- ``yield_quality_score(f)`` — ``1 - (payout_ratio / payout_max)``;
  measures dividend safety. Lower payout ⇒ safer payout. Clamped
  to [0, 1].
- ``valuation_score(f)`` — ``1 - (debt_equity / debt_equity_max)``;
  lower leverage is treated as a better valuation proxy in the
  absence of explicit P/E or P/FCF inputs. Clamped to [0, 1].

These choices are documented here so the next data-layer enrichment
can swap them without changing the public API.

REQ refs: REQ_F_SCR_001, REQ_F_SCR_002, REQ_SDD_ALG_018,
REQ_SDS_MOD_006, REQ_SDD_API_001 (read-only over state).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Fundamentals
from trading_system.models.instrument import Stock
from trading_system.result import Err, Ok
from trading_system.screener.config import ScreenerConfig

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """Per-component scores. All values lie in [0, 1]."""

    stability: Decimal
    yield_quality: Decimal
    valuation: Decimal

    def __post_init__(self) -> None:
        for label, v in (
            ("stability", self.stability),
            ("yield_quality", self.yield_quality),
            ("valuation", self.valuation),
        ):
            if not (Decimal(0) <= v <= Decimal(1)):
                raise ValueError(f"ScoreBreakdown.{label} must lie in [0, 1], got {v}")


@dataclass(frozen=True, slots=True)
class ScoredStock:
    """A stock that passed the filter, with its weighted total score
    and the per-component breakdown for diagnostics."""

    stock: Stock
    score: Decimal
    breakdown: ScoreBreakdown

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.score <= Decimal(1)):
            raise ValueError(f"ScoredStock.score must lie in [0, 1], got {self.score}")


# ---------------------------------------------------------------------------
# Filter — observable rule order (REQ_SDD_ALG_018)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FilterRule:
    """A named filter predicate.

    The ``FILTER_RULES`` tuple below pins the evaluation order so that
    REQ_SDD_ALG_018 ("yield -> payout -> FCF -> D/E -> history,
    cheapest first") is observable from outside without instrumenting
    the predicate calls.
    """

    name: str
    predicate: Callable[[Fundamentals, ScreenerConfig], bool]


def _yield_in_band(f: Fundamentals, cfg: ScreenerConfig) -> bool:
    return cfg.yield_min <= f.yield_ <= cfg.yield_max


def _payout_below_max(f: Fundamentals, cfg: ScreenerConfig) -> bool:
    return f.payout_ratio < cfg.payout_max


def _free_cash_flow_positive(f: Fundamentals, _cfg: ScreenerConfig) -> bool:
    return f.free_cash_flow.amount > 0


def _debt_equity_below_max(f: Fundamentals, cfg: ScreenerConfig) -> bool:
    return f.debt_equity < cfg.debt_equity_max


def _history_at_least(f: Fundamentals, cfg: ScreenerConfig) -> bool:
    return f.dividend_history_years >= cfg.min_history_years


FILTER_RULES: tuple[FilterRule, ...] = (
    FilterRule("yield", _yield_in_band),
    FilterRule("payout", _payout_below_max),
    FilterRule("free_cash_flow", _free_cash_flow_positive),
    FilterRule("debt_equity", _debt_equity_below_max),
    FilterRule("history", _history_at_least),
)


def _passes(f: Fundamentals, cfg: ScreenerConfig) -> bool:
    """Run every filter rule in order; short-circuit on the first
    failure (REQ_SDD_ALG_018). ``all`` over a generator gives the
    short-circuit semantics we want without changing the rule order."""
    return all(rule.predicate(f, cfg) for rule in FILTER_RULES)


# ---------------------------------------------------------------------------
# Score components
# ---------------------------------------------------------------------------


def _clamp01(value: Decimal) -> Decimal:
    if value < Decimal(0):
        return Decimal(0)
    if value > Decimal(1):
        return Decimal(1)
    return value


def stability_score(f: Fundamentals, cfg: ScreenerConfig) -> Decimal:
    """Linear ramp on dividend history; saturates at
    ``cfg.stability_full_years``."""
    if cfg.stability_full_years <= 0:
        raise ValueError("stability_full_years must be > 0")
    return _clamp01(Decimal(f.dividend_history_years) / Decimal(cfg.stability_full_years))


def yield_quality_score(f: Fundamentals, cfg: ScreenerConfig) -> Decimal:
    """``1 - payout_ratio / payout_max`` clamped — lower payout means
    a safer dividend; this score is *quality*, not *level* (the level
    is already gated by the yield band filter)."""
    if cfg.payout_max <= 0:
        raise ValueError("payout_max must be > 0")
    return _clamp01(Decimal(1) - f.payout_ratio / cfg.payout_max)


def valuation_score(f: Fundamentals, cfg: ScreenerConfig) -> Decimal:
    """Leverage-based valuation proxy: ``1 - debt_equity /
    debt_equity_max`` clamped."""
    if cfg.debt_equity_max <= 0:
        raise ValueError("debt_equity_max must be > 0")
    return _clamp01(Decimal(1) - f.debt_equity / cfg.debt_equity_max)


def _score(f: Fundamentals, cfg: ScreenerConfig) -> tuple[Decimal, ScoreBreakdown]:
    breakdown = ScoreBreakdown(
        stability=stability_score(f, cfg),
        yield_quality=yield_quality_score(f, cfg),
        valuation=valuation_score(f, cfg),
    )
    w = cfg.weights
    total = w[0] * breakdown.stability + w[1] * breakdown.yield_quality + w[2] * breakdown.valuation
    return _clamp01(total), breakdown


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def screen(
    universe: Sequence[Stock],
    data: MarketDataProvider,
    cfg: ScreenerConfig,
) -> list[ScoredStock]:
    """Filter and rank ``universe``. Stocks whose fundamentals are
    unavailable (``Err`` from the provider) or fail any filter rule
    are silently dropped. Output is sorted by total score descending;
    ties keep the original universe order (Python ``sorted`` is
    stable)."""
    out: list[ScoredStock] = []
    for stock in universe:
        result = data.fundamentals(stock)
        match result:
            case Err(_):
                continue
            case Ok(fundamentals):
                if not _passes(fundamentals, cfg):
                    continue
                total, breakdown = _score(fundamentals, cfg)
                out.append(ScoredStock(stock=stock, score=total, breakdown=breakdown))
    out.sort(key=lambda x: x.score, reverse=True)
    return out
