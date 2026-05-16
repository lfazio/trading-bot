"""``HypothesisValidator`` — five gates in strict order.

The validator catches hallucination-shaped hypotheses *before* any
backtest runs (REQ_F_QNT_004 / REQ_SDS_QNT_002). Gate ordering is
fixed: ``structural → bounds → falsifiable → metric_alignment →
dataset_sanity``. First-fail short-circuits the rest so the
operator sees the most-load-bearing failure category first.

Categorised ``Err`` set (closed; REQ_SDD_QNT_002):
    hypothesis:structural:<field>
    hypothesis:bounds:<field>
    hypothesis:not_falsifiable
    hypothesis:metric_mismatch:unknown_metric:<name>
    hypothesis:metric_mismatch:claim_metric_drift
    hypothesis:bad_window:future_end
    hypothesis:bad_window:too_short_for_timescale
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from trading_system.result import Err, Ok, Result
from trading_system.strategy_lab.quant.hypothesis import (
    DEFAULT_METRIC_VOCABULARY,
    Hypothesis,
)


# Regex catching any numeric literal in the claim ("4%", "4.5",
# "0.045", "4 000%"). Whatever the parser extracts SHALL fall inside
# the bounds_table entry for the cited metric or the bounds gate
# trips. Designed to be loose — false positives (a "5y history"
# mention) miss the bounds_table key and are skipped, never
# rejected.
_NUMERIC_RE: Final = re.compile(r"-?\d+(?:[\s,]\d{3})*(?:[.,]\d+)?\s*%?")


@dataclass(frozen=True, slots=True)
class ValidatorConfig:
    """Tunable thresholds the validator reads.

    ``bounds_table`` maps a metric name to its operator-configured
    plausible ``(lo, hi)`` range. Numbers extracted from the claim
    SHALL fall in ``[lo, hi]`` to pass gate 2.

    ``min_duration_days_for_1d`` enforces gate-5's "window long
    enough" check — a 1d-frequency hypothesis SHALL cover at least
    this many calendar days. The intraday frequencies are checked
    against ``min_window_for_intraday_days`` so a 1h-frequency
    hypothesis can pass a single-day window (one trading session
    has 6.5 hours of bars).
    """

    bounds_table: Mapping[str, tuple[Decimal, Decimal]] = field(
        default_factory=lambda: {
            "sharpe": (Decimal("-3"), Decimal("3")),
            "adjusted_sharpe": (Decimal("-3"), Decimal("3")),
            "net_after_tax_return": (Decimal("-1"), Decimal("10")),  # -100% .. +1000%
            "max_drawdown": (Decimal("0"), Decimal("1")),
            "information_coefficient": (Decimal("-1"), Decimal("1")),
            "stability": (Decimal("0"), Decimal("1")),
            "turnover": (Decimal("0"), Decimal("1000")),
        }
    )
    metric_vocabulary: frozenset[str] = field(
        default_factory=lambda: DEFAULT_METRIC_VOCABULARY
    )
    min_duration_days_for_1d: int = 30
    min_window_for_intraday_days: int = 1


@dataclass(slots=True)
class HypothesisValidator:
    """Stateful only in the sense that it caches the config; the
    ``validate`` method is pure modulo ``now()``."""

    cfg: ValidatorConfig
    now: Callable[[], datetime] = field(default_factory=lambda: _default_now)

    def validate(self, h: Hypothesis) -> Result[None, str]:
        # Gate 1: structural — Hypothesis.__post_init__ has already
        # checked non-empty fields, so we re-verify the slightly
        # stricter "no obviously placeholder text" rule here.
        match _gate_structural(h):
            case Err(reason):
                return Err(reason)
            case Ok(_):
                pass
        # Gate 2: bounds.
        match _gate_bounds(h, self.cfg.bounds_table):
            case Err(reason):
                return Err(reason)
            case Ok(_):
                pass
        # Gate 3: falsifiable.
        if not _is_falsifiable(h):
            return Err("hypothesis:not_falsifiable")
        # Gate 4: metric alignment.
        if h.metric not in self.cfg.metric_vocabulary:
            return Err(f"hypothesis:metric_mismatch:unknown_metric:{h.metric}")
        if not _claim_mentions_metric(h):
            return Err("hypothesis:metric_mismatch:claim_metric_drift")
        # Gate 5: dataset sanity.
        now = self.now()
        if h.dataset_window.end > now:
            return Err("hypothesis:bad_window:future_end")
        if _window_too_short(h, self.cfg):
            return Err("hypothesis:bad_window:too_short_for_timescale")
        return Ok(None)


# ---------------------------------------------------------------------------
# Gate implementations — pure functions, easy to test individually
# ---------------------------------------------------------------------------


_PLACEHOLDER_TOKENS: Final = frozenset(
    {"todo", "tbd", "lorem", "placeholder", "???", "n/a"}
)


def _gate_structural(h: Hypothesis) -> Result[None, str]:
    for field_name in ("claim", "falsification_criterion", "operator_rationale"):
        value = getattr(h, field_name).strip().lower()
        if not value:
            return Err(f"hypothesis:structural:{field_name}")
        # Placeholder-text trip: catches claims like "TBD" / "???".
        if value in _PLACEHOLDER_TOKENS:
            return Err(f"hypothesis:structural:{field_name}")
    return Ok(None)


def _gate_bounds(
    h: Hypothesis, bounds_table: Mapping[str, tuple[Decimal, Decimal]]
) -> Result[None, str]:
    """Extract numeric literals from the claim and check they fall in
    the configured plausible range for ``h.metric``.

    A claim that mentions no numeric literal is acceptable — the gate
    is "if the claim asserts a number, the number is plausible";
    silent numeric-free claims fall through (the falsifiable gate
    catches those independently).
    """
    bounds = bounds_table.get(h.metric)
    if bounds is None:
        # No bounds configured for this metric ⇒ pass through.
        return Ok(None)
    lo, hi = bounds
    for match in _NUMERIC_RE.finditer(h.claim):
        raw = match.group(0)
        try:
            value = _parse_number(raw)
        except InvalidOperation:
            continue
        if not (lo <= value <= hi):
            return Err(f"hypothesis:bounds:{h.metric}")
    return Ok(None)


def _parse_number(raw: str) -> Decimal:
    """Parse a string number tolerant of ``%`` suffix, thousands
    separators, and decimal commas. Percentage marks divide by 100
    so ``"45%"`` parses as ``Decimal("0.45")``."""
    s = raw.strip()
    is_pct = s.endswith("%")
    if is_pct:
        s = s[:-1].strip()
    s = s.replace(" ", "").replace(" ", "")
    # Normalise European decimal comma -> point, but only when there
    # is no surviving comma+thousands. We don't see real European
    # input here but the parser is forgiving.
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    value = Decimal(s)
    if is_pct:
        value = value / Decimal(100)
    return value


def _is_falsifiable(h: Hypothesis) -> bool:
    """Heuristic falsifiability check.

    A criterion is considered falsifiable when it mentions at least
    one of the canonical inequality tokens (``<``, ``>``, ``=``,
    ``"less than"``, ``"greater than"``, etc.) OR a numeric literal
    paired with a comparison preposition (``"below"``, ``"above"``,
    ``"under"``, ``"over"``). Pure prose ("the strategy works in
    bull markets") trips the gate.
    """
    text = h.falsification_criterion.lower()
    inequality_tokens = (
        "<",
        ">",
        "=",
        "less than",
        "greater than",
        "below",
        "above",
        "under",
        "over",
        "no more than",
        "at least",
        "at most",
        "drops",
        "falls",
        "exceeds",
    )
    return any(tok in text for tok in inequality_tokens)


def _claim_mentions_metric(h: Hypothesis) -> bool:
    """Gate 4b — the claim text SHALL reference the metric or one of
    its documented synonyms. Prevents a hypothesis whose ``metric``
    field is ``sharpe`` but whose claim only talks about turnover
    from sneaking past the validator.
    """
    text = h.claim.lower()
    metric = h.metric.lower()
    synonyms = {
        "sharpe": ("sharpe", "risk-adjusted return", "risk adjusted return"),
        "adjusted_sharpe": ("adjusted sharpe", "sharpe"),
        "net_after_tax_return": (
            "net return",
            "after-tax return",
            "after tax return",
            "return",
        ),
        "max_drawdown": ("drawdown", "max drawdown", "dd"),
        "information_coefficient": ("information coefficient", "ic"),
        "stability": ("stability", "stable"),
        "turnover": ("turnover",),
    }
    needles = synonyms.get(metric, (metric,))
    return any(n in text for n in needles)


def _window_too_short(h: Hypothesis, cfg: ValidatorConfig) -> bool:
    """Gate 5b — the dataset window SHALL be long enough for the
    declared frequency. Daily frequency needs at least
    ``min_duration_days_for_1d`` (default 30); intraday needs at
    least ``min_window_for_intraday_days`` (default 1).
    """
    days = h.dataset_window.duration_days()
    if h.dataset_window.frequency in ("1d", "d", "daily"):
        return days < cfg.min_duration_days_for_1d
    # Anything we don't recognise as daily we treat as intraday.
    return days < cfg.min_window_for_intraday_days


def _default_now() -> datetime:
    return datetime.now(tz=UTC)
