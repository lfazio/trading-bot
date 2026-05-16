"""Tests for ``HypothesisLibrary`` + ``InMemoryHypothesisStore``
(REQ_F_QNT_002, REQ_SDS_QNT_003, REQ_SDD_QNT_003)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal  # noqa: F401 — re-exported for completeness

from trading_system.result import Err, Nothing, Ok, Some
from trading_system.strategy_lab.quant.hypothesis import (
    DatasetWindow,
    Direction,
    Hypothesis,
    HypothesisId,
    HypothesisState,
)
from trading_system.strategy_lab.quant.library import (
    HypothesisLibrary,
    HypothesisStore,
    InMemoryHypothesisStore,
)


def _h(
    hid: str = "h-1",
    *,
    state: HypothesisState = HypothesisState.PENDING,
    created_at: datetime | None = None,
) -> Hypothesis:
    return Hypothesis(
        id=HypothesisId(hid),
        claim="adjusted_sharpe of dividend aristocrats stays above 1.2",
        falsification_criterion="reject if adjusted_sharpe < 0.8",
        dataset_window=DatasetWindow(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 6, 1, tzinfo=UTC),
            frequency="1d",
        ),
        metric="adjusted_sharpe",
        expected_direction=Direction.POSITIVE,
        operator_rationale="quality composite reduces vol",
        created_at=created_at or datetime(2026, 5, 16, tzinfo=UTC),
        state=state,
    )


def _lib() -> HypothesisLibrary:
    return HypothesisLibrary(store=InMemoryHypothesisStore())


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_in_memory_store_satisfies_protocol() -> None:
    assert isinstance(InMemoryHypothesisStore(), HypothesisStore)


# ---------------------------------------------------------------------------
# store_pending
# ---------------------------------------------------------------------------


def test_store_pending_inserts() -> None:
    lib = _lib()
    assert isinstance(lib.store_pending(_h()), Ok)


def test_store_pending_rejects_duplicate_id() -> None:
    lib = _lib()
    lib.store_pending(_h()).unwrap()
    match lib.store_pending(_h()):
        case Err(reason):
            assert reason == "hypothesis:duplicate_id:h-1"
        case _:
            raise AssertionError("expected Err")


def test_store_pending_rejects_non_pending_initial_state() -> None:
    lib = _lib()
    match lib.store_pending(_h(state=HypothesisState.VALIDATED)):
        case Err(reason):
            assert reason.startswith("hypothesis:bad_initial_state:")
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


def test_transition_to_validated() -> None:
    lib = _lib()
    lib.store_pending(_h()).unwrap()
    res = lib.transition(
        HypothesisId("h-1"),
        HypothesisState.VALIDATED,
        "backtest passed",
        at=datetime(2026, 5, 16, 12, tzinfo=UTC),
    )
    assert isinstance(res, Ok)


def test_transition_to_rejected() -> None:
    lib = _lib()
    lib.store_pending(_h()).unwrap()
    res = lib.transition(
        HypothesisId("h-1"),
        HypothesisState.REJECTED,
        "overfitting:parameter_to_data_ratio:0.10",
        at=datetime(2026, 5, 16, 12, tzinfo=UTC),
    )
    assert isinstance(res, Ok)


def test_transition_to_pending_rejected() -> None:
    lib = _lib()
    lib.store_pending(_h()).unwrap()
    match lib.transition(
        HypothesisId("h-1"),
        HypothesisState.PENDING,
        "noop",
        at=datetime(2026, 5, 16, 12, tzinfo=UTC),
    ):
        case Err(reason):
            assert reason == "hypothesis:bad_transition:cannot_revert_to_pending"
        case _:
            raise AssertionError("expected Err")


def test_transition_empty_reason_rejected() -> None:
    lib = _lib()
    lib.store_pending(_h()).unwrap()
    match lib.transition(
        HypothesisId("h-1"),
        HypothesisState.VALIDATED,
        "   ",
        at=datetime(2026, 5, 16, 12, tzinfo=UTC),
    ):
        case Err(reason):
            assert reason == "hypothesis:bad_transition:empty_reason"
        case _:
            raise AssertionError("expected Err")


def test_transition_unknown_id_rejected() -> None:
    lib = _lib()
    match lib.transition(
        HypothesisId("ghost"),
        HypothesisState.VALIDATED,
        "what",
        at=datetime(2026, 5, 16, 12, tzinfo=UTC),
    ):
        case Err(reason):
            assert reason == "hypothesis:not_found:ghost"
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_returns_some_when_present() -> None:
    lib = _lib()
    lib.store_pending(_h()).unwrap()
    res = lib.get(HypothesisId("h-1")).unwrap()
    match res:
        case Some(h):
            assert str(h.id) == "h-1"
        case _:
            raise AssertionError("expected Some")


def test_get_returns_nothing_when_absent() -> None:
    lib = _lib()
    res = lib.get(HypothesisId("ghost")).unwrap()
    assert isinstance(res, Nothing)


# ---------------------------------------------------------------------------
# list_by_state — current-state lookup is authoritative
# ---------------------------------------------------------------------------


def test_list_by_state_returns_only_matching_current_state() -> None:
    lib = _lib()
    lib.store_pending(_h("a", created_at=datetime(2026, 5, 10, tzinfo=UTC))).unwrap()
    lib.store_pending(_h("b", created_at=datetime(2026, 5, 11, tzinfo=UTC))).unwrap()
    lib.store_pending(_h("c", created_at=datetime(2026, 5, 12, tzinfo=UTC))).unwrap()
    # Transition b to validated; a + c stay pending.
    lib.transition(
        HypothesisId("b"),
        HypothesisState.VALIDATED,
        "passed",
        at=datetime(2026, 5, 13, tzinfo=UTC),
    ).unwrap()

    pending = lib.list_by_state(HypothesisState.PENDING).unwrap()
    validated = lib.list_by_state(HypothesisState.VALIDATED).unwrap()
    assert tuple(str(h.id) for h in pending) == ("a", "c")
    assert tuple(str(h.id) for h in validated) == ("b",)


def test_list_by_state_sorted_by_created_at_ascending() -> None:
    """REQ_NF_QNT_002 — deterministic iteration order."""
    lib = _lib()
    lib.store_pending(_h("c", created_at=datetime(2026, 5, 12, tzinfo=UTC))).unwrap()
    lib.store_pending(_h("a", created_at=datetime(2026, 5, 10, tzinfo=UTC))).unwrap()
    lib.store_pending(_h("b", created_at=datetime(2026, 5, 11, tzinfo=UTC))).unwrap()
    pending = lib.list_by_state(HypothesisState.PENDING).unwrap()
    assert tuple(str(h.id) for h in pending) == ("a", "b", "c")


# ---------------------------------------------------------------------------
# transitions_for — audit-log access
# ---------------------------------------------------------------------------


def test_transitions_for_records_audit_rows() -> None:
    lib = _lib()
    lib.store_pending(_h()).unwrap()
    t = datetime(2026, 5, 16, 12, tzinfo=UTC)
    lib.transition(
        HypothesisId("h-1"), HypothesisState.VALIDATED, "passed", at=t
    ).unwrap()
    rows = lib.transitions_for(HypothesisId("h-1")).unwrap()
    assert len(rows) == 1
    assert rows[0].new_state is HypothesisState.VALIDATED
    assert rows[0].reason == "passed"
    assert rows[0].transitioned_at == t


def test_current_state_uses_latest_transition() -> None:
    store = InMemoryHypothesisStore()
    lib = HypothesisLibrary(store=store)
    lib.store_pending(_h()).unwrap()
    # Single transition wins; verify by inspecting current_state.
    lib.transition(
        HypothesisId("h-1"),
        HypothesisState.VALIDATED,
        "x",
        at=datetime(2026, 5, 16, 12, tzinfo=UTC),
    ).unwrap()
    current = store.current_state(HypothesisId("h-1")).unwrap()
    match current:
        case Some(state):
            assert state is HypothesisState.VALIDATED
        case _:
            raise AssertionError("expected Some")
