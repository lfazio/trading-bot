"""Tests for ``trading_system.regime.transition``.

Covers TC_RGM_007 (confirmation window + flip-back reset),
TC_RGM_010 partial (restart rehydration via from_seed).

REQ refs: REQ_F_RGM_004, REQ_SDS_RGM_002, REQ_SDD_RGM_003,
REQ_NF_RGM_001.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_system.models.phase import MarketRegime
from trading_system.regime.transition import TransitionEvent, TransitionTracker
from trading_system.result import Nothing, Some


def _at(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


# ---------------------------------------------------------------------------
# TransitionEvent invariants
# ---------------------------------------------------------------------------


def test_transition_event_rejects_identical_from_and_to() -> None:
    with pytest.raises(ValueError, match="from_regime and to_regime must differ"):
        TransitionEvent(
            from_regime=MarketRegime.BULL,
            to_regime=MarketRegime.BULL,
            at=_at(1),
            confirmation_periods=2,
        )


def test_transition_event_rejects_zero_confirmation_periods() -> None:
    with pytest.raises(ValueError, match="confirmation_periods"):
        TransitionEvent(
            from_regime=MarketRegime.BULL,
            to_regime=MarketRegime.BEAR,
            at=_at(1),
            confirmation_periods=0,
        )


def test_transition_event_is_frozen() -> None:
    event = TransitionEvent(
        from_regime=MarketRegime.BULL,
        to_regime=MarketRegime.BEAR,
        at=_at(1),
        confirmation_periods=2,
    )
    with pytest.raises(Exception):
        event.from_regime = MarketRegime.SIDEWAYS  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TC_RGM_007 — confirmation window + flip-back reset
# ---------------------------------------------------------------------------


def test_first_observation_seeds_cursor_without_emitting() -> None:
    tracker = TransitionTracker(confirmation_periods=2)
    res = tracker.observe(MarketRegime.BULL, at=_at(1))
    assert isinstance(res, Nothing)
    match tracker.current_regime:
        case Some(r):
            assert r is MarketRegime.BULL
        case _:
            raise AssertionError("expected cursor seeded to BULL")


def test_stable_observation_returns_nothing() -> None:
    tracker = TransitionTracker(confirmation_periods=2)
    tracker.observe(MarketRegime.BULL, at=_at(1))
    res = tracker.observe(MarketRegime.BULL, at=_at(2))
    assert isinstance(res, Nothing)


def test_transition_emits_only_after_confirmation_window() -> None:
    tracker = TransitionTracker(confirmation_periods=2)
    tracker.observe(MarketRegime.BULL, at=_at(1))
    # First BEAR observation — count = 1, below the 2-period threshold.
    res1 = tracker.observe(MarketRegime.BEAR, at=_at(2))
    assert isinstance(res1, Nothing)
    # Second BEAR observation — count = 2 — emit.
    res2 = tracker.observe(MarketRegime.BEAR, at=_at(3))
    match res2:
        case Some(event):
            assert event.from_regime is MarketRegime.BULL
            assert event.to_regime is MarketRegime.BEAR
            assert event.at == _at(3)
            assert event.confirmation_periods == 2
        case _:
            raise AssertionError("expected confirmed transition")


def test_flip_back_resets_candidate_window() -> None:
    """REQ_F_RGM_004 — a regime that flips back before reaching the
    confirmation threshold SHALL NOT emit a transition; the candidate
    window resets."""
    tracker = TransitionTracker(confirmation_periods=3)
    tracker.observe(MarketRegime.BULL, at=_at(1))
    # 2 BEAR observations — count = 2, below the 3-period threshold.
    assert isinstance(tracker.observe(MarketRegime.BEAR, at=_at(2)), Nothing)
    assert isinstance(tracker.observe(MarketRegime.BEAR, at=_at(3)), Nothing)
    # Flip back to BULL — should reset the candidate window.
    assert isinstance(tracker.observe(MarketRegime.BULL, at=_at(4)), Nothing)
    # 2 more BEAR observations — should NOT emit because the window reset.
    assert isinstance(tracker.observe(MarketRegime.BEAR, at=_at(5)), Nothing)
    assert isinstance(tracker.observe(MarketRegime.BEAR, at=_at(6)), Nothing)
    # The 3rd BEAR in a row finally emits.
    res = tracker.observe(MarketRegime.BEAR, at=_at(7))
    match res:
        case Some(event):
            assert event.from_regime is MarketRegime.BULL
            assert event.to_regime is MarketRegime.BEAR
        case _:
            raise AssertionError("expected confirmed transition after fresh window")


def test_alternating_candidates_restart_count() -> None:
    """A BEAR then HIGH_VOL then BEAR should start a fresh BEAR
    window, not accumulate across different candidates."""
    tracker = TransitionTracker(confirmation_periods=2)
    tracker.observe(MarketRegime.BULL, at=_at(1))
    # BEAR observation: candidate=BEAR, count=1.
    assert isinstance(tracker.observe(MarketRegime.BEAR, at=_at(2)), Nothing)
    # HIGH_VOL observation: candidate=HIGH_VOL, count=1 (restart).
    assert isinstance(tracker.observe(MarketRegime.HIGH_VOL, at=_at(3)), Nothing)
    # Another HIGH_VOL: count=2 — emit BULL → HIGH_VOL.
    res = tracker.observe(MarketRegime.HIGH_VOL, at=_at(4))
    match res:
        case Some(event):
            assert event.from_regime is MarketRegime.BULL
            assert event.to_regime is MarketRegime.HIGH_VOL
        case _:
            raise AssertionError("expected BULL → HIGH_VOL transition")


def test_replay_with_same_observations_is_deterministic() -> None:
    """REQ_NF_RGM_001 — identical observation sequences produce
    identical TransitionEvent emissions."""
    observations = [
        (MarketRegime.BULL, _at(1)),
        (MarketRegime.BEAR, _at(2)),
        (MarketRegime.BEAR, _at(3)),
        (MarketRegime.BEAR, _at(4)),
    ]
    t1 = TransitionTracker(confirmation_periods=2)
    t2 = TransitionTracker(confirmation_periods=2)
    out1 = [t1.observe(r, at=at) for r, at in observations]
    out2 = [t2.observe(r, at=at) for r, at in observations]
    # Compare the emissions: Nothing or Some(event)
    for o1, o2 in zip(out1, out2):
        match o1, o2:
            case Nothing(), Nothing():
                pass
            case Some(e1), Some(e2):
                assert e1 == e2
            case _:
                raise AssertionError(
                    f"replay determinism violated: {o1!r} vs {o2!r}"
                )


# ---------------------------------------------------------------------------
# TC_RGM_010 — restart rehydration via from_seed
# ---------------------------------------------------------------------------


def test_from_seed_seeds_cursor_to_persisted_regime() -> None:
    tracker = TransitionTracker.from_seed(
        confirmation_periods=2,
        current=MarketRegime.BEAR,
    )
    match tracker.current_regime:
        case Some(r):
            assert r is MarketRegime.BEAR
        case _:
            raise AssertionError("expected cursor seeded to BEAR")
    # Subsequent BEAR observation is stable — no event.
    assert isinstance(tracker.observe(MarketRegime.BEAR, at=_at(10)), Nothing)


def test_from_seed_supports_subsequent_transition() -> None:
    tracker = TransitionTracker.from_seed(
        confirmation_periods=2,
        current=MarketRegime.BEAR,
    )
    tracker.observe(MarketRegime.BULL, at=_at(11))
    res = tracker.observe(MarketRegime.BULL, at=_at(12))
    match res:
        case Some(event):
            # from_regime SHALL be the rehydrated cursor's regime.
            assert event.from_regime is MarketRegime.BEAR
            assert event.to_regime is MarketRegime.BULL
        case _:
            raise AssertionError("expected BEAR → BULL transition after seed")


def test_confirmation_periods_must_be_positive_in_constructor() -> None:
    with pytest.raises(ValueError, match="confirmation_periods"):
        TransitionTracker(confirmation_periods=0)
    with pytest.raises(ValueError, match="confirmation_periods"):
        TransitionTracker.from_seed(
            confirmation_periods=0, current=MarketRegime.BULL
        )
