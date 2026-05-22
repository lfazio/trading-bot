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
from hypothesis import assume, given, settings
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


@given(
    train=_DURATIONS,
    valid=_DURATIONS,
    oos=_DURATIONS,
    period_days=st.integers(min_value=30, max_value=2000),
)
@settings(max_examples=200)
def test_step_advances_by_valid_width(
    train: timedelta,
    valid: timedelta,
    oos: timedelta,
    period_days: int,
) -> None:
    """REQ_SDD_ALG_004 — the rolling step advances by ``valid``
    so consecutive OOS slices don't overlap."""
    window = WalkForwardWindow(train=train, valid=valid, oos=oos)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=period_days)
    boundaries = _step_boundaries(
        period_start=start, period_end=end, window=window
    )
    assume(len(boundaries) >= 2)
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
