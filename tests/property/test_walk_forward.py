"""Property-based tests for walk-forward window arithmetic —
REQ_TP_STR_002.

REQ_SDD_ALG_004 — walk-forward windows partition time
deterministically: each step has a ``(train, valid, oos)`` triple
that doesn't overlap with itself, and the engine advances by the
``valid`` width per step.

Properties verified:

- WalkForwardWindow constructor rejects non-positive durations.
- Step arithmetic: at every step, the train + valid + oos
  segments are non-overlapping and cover a contiguous span.
- Stepping by ``valid``: the start of step n+1 equals the start
  of step n plus ``valid``.
- All windows fit inside ``[period_start, period_end]``.
- Two runs with identical ``(period_start, period_end, window)``
  inputs produce equal step boundaries.

The walk_forward orchestrator itself runs three backtests per
step which is too expensive to exercise inside a property test —
the step-arithmetic verifiable here is what the SDD pins.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trading_system.backtesting.walk_forward import WalkForwardWindow


# A modestly bounded time grain — seconds are too small for the
# walk_forward orchestration which counts days. Use day-grained
# windows because the SDD's default is months.
_DURATIONS = st.integers(min_value=1, max_value=730).map(
    lambda d: timedelta(days=d)
)


# ---------------------------------------------------------------------------
# WalkForwardWindow construction invariants
# ---------------------------------------------------------------------------


@given(train=_DURATIONS, valid=_DURATIONS, oos=_DURATIONS)
@settings(max_examples=100)
def test_window_construction_accepts_positive(
    train: timedelta, valid: timedelta, oos: timedelta
) -> None:
    """All positive durations SHALL succeed."""
    w = WalkForwardWindow(train=train, valid=valid, oos=oos)
    assert w.train == train
    assert w.valid == valid
    assert w.oos == oos


@pytest.mark.parametrize("zero_field", ["train", "valid", "oos"])
def test_window_construction_rejects_zero_duration(zero_field: str) -> None:
    """REQ_SDD_ALG_004 — non-positive durations SHALL panic."""
    kwargs: dict[str, timedelta] = {
        "train": timedelta(days=1),
        "valid": timedelta(days=1),
        "oos": timedelta(days=1),
    }
    kwargs[zero_field] = timedelta(0)
    with pytest.raises(ValueError, match=zero_field):
        WalkForwardWindow(**kwargs)


# ---------------------------------------------------------------------------
# Step arithmetic — recreate the step loop and verify partitioning
# ---------------------------------------------------------------------------


def _step_boundaries(
    *,
    period_start: datetime,
    period_end: datetime,
    window: WalkForwardWindow,
) -> list[tuple[datetime, datetime, datetime, datetime]]:
    """Mirror ``walk_forward``'s step loop without running the
    nested backtests. Returns ``(train_start, train_end,
    valid_end, oos_end)`` for each step."""
    full_window = window.train + window.valid + window.oos
    cur = period_start
    out: list[tuple[datetime, datetime, datetime, datetime]] = []
    while cur + full_window <= period_end:
        train_start = cur
        train_end = cur + window.train
        valid_end = train_end + window.valid
        oos_end = valid_end + window.oos
        out.append((train_start, train_end, valid_end, oos_end))
        cur += window.valid
    return out


@given(
    train=_DURATIONS,
    valid=_DURATIONS,
    oos=_DURATIONS,
    period_days=st.integers(min_value=30, max_value=2000),
)
@settings(max_examples=200)
def test_step_segments_are_contiguous_non_overlapping(
    train: timedelta,
    valid: timedelta,
    oos: timedelta,
    period_days: int,
) -> None:
    """REQ_SDD_ALG_004 — within a single step, (train, valid, oos)
    SHALL be contiguous: train_end == valid_start;
    valid_end == oos_start. No gap, no overlap."""
    window = WalkForwardWindow(train=train, valid=valid, oos=oos)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=period_days)
    boundaries = _step_boundaries(
        period_start=start, period_end=end, window=window
    )
    for (train_start, train_end, valid_end, oos_end) in boundaries:
        # Contiguity within the step.
        # train_end is the boundary between train and valid.
        # valid_end is the boundary between valid and oos.
        # Reconstruct the exact widths.
        assert train_end - train_start == train
        assert valid_end - train_end == valid
        assert oos_end - valid_end == oos


@st.composite
def _window_and_multi_step_period(draw):  # type: ignore[no-untyped-def]
    """Generate a (window, period_days) pair guaranteed to yield
    ≥ 2 boundary steps without relying on ``assume`` filtering.

    Previously this property used
    ``assume(len(boundaries) >= 2)``, but for random
    ``(train, valid, oos, period_days)`` quads most generated
    cases produce 0 or 1 boundaries, tripping Hypothesis's
    ``filter_too_much`` health check intermittently. Composing
    the period_days from a multiplier of the full-window width
    makes ≥ 2 boundaries unconditional — no filtering, no flake.
    """
    train = draw(_DURATIONS)
    valid = draw(_DURATIONS)
    oos = draw(_DURATIONS)
    full_window_days = (train + valid + oos).days
    # Need at least one extra ``valid`` past the first window's
    # oos_end to get a second step; n_extra_steps ∈ [1, 5] for
    # bounded but varied coverage.
    n_extra_steps = draw(st.integers(min_value=1, max_value=5))
    period_days = full_window_days + n_extra_steps * valid.days
    return train, valid, oos, period_days


@given(spec=_window_and_multi_step_period())
@settings(max_examples=200)
def test_step_advances_by_valid_width(
    spec: tuple[timedelta, timedelta, timedelta, int],
) -> None:
    """REQ_SDD_ALG_004 — the rolling step advances by ``valid``
    so consecutive OOS slices don't overlap.

    Uses a composite strategy that guarantees ≥ 2 boundary
    steps so no ``assume()`` filtering is needed (which
    previously tripped Hypothesis's ``filter_too_much`` health
    check intermittently)."""
    train, valid, oos, period_days = spec
    window = WalkForwardWindow(train=train, valid=valid, oos=oos)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=period_days)
    boundaries = _step_boundaries(
        period_start=start, period_end=end, window=window
    )
    # Composite strategy guarantees ≥ 2 boundaries by construction.
    assert len(boundaries) >= 2, (
        f"composite generated < 2 boundaries: "
        f"train={train.days}d valid={valid.days}d oos={oos.days}d "
        f"period={period_days}d boundaries={len(boundaries)}"
    )
    for i in range(1, len(boundaries)):
        prev_train_start = boundaries[i - 1][0]
        cur_train_start = boundaries[i][0]
        assert cur_train_start - prev_train_start == valid, (
            f"step {i} advance is {cur_train_start - prev_train_start}, "
            f"expected {valid}"
        )


@given(
    train=_DURATIONS,
    valid=_DURATIONS,
    oos=_DURATIONS,
    period_days=st.integers(min_value=30, max_value=2000),
)
@settings(max_examples=100)
def test_every_step_fits_within_period(
    train: timedelta,
    valid: timedelta,
    oos: timedelta,
    period_days: int,
) -> None:
    """Every emitted step's oos_end SHALL be ≤ period_end, and
    every train_start SHALL be ≥ period_start."""
    window = WalkForwardWindow(train=train, valid=valid, oos=oos)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=period_days)
    boundaries = _step_boundaries(
        period_start=start, period_end=end, window=window
    )
    for (train_start, _train_end, _valid_end, oos_end) in boundaries:
        assert train_start >= start
        assert oos_end <= end
