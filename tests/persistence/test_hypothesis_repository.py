"""``HypothesisRepository`` tests — CR-002 Phase B / REQ_SDD_QNT_007.

REQ refs:
- REQ_F_QNT_001 — three-state lifecycle persisted faithfully.
- REQ_NF_QNT_002 — deterministic iteration; round-trip equality.
- REQ_F_PER_002 / REQ_F_PER_003 / REQ_F_PER_009 — repo per
  aggregate, explicit transactions, account_id-keyed.
- REQ_SDS_PER_002 — closed Err category set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories import HypothesisRepository
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.strategy_lab.quant.hypothesis import (
    DatasetWindow,
    Direction,
    Hypothesis,
    HypothesisId,
    HypothesisState,
)


_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "trading_system"
    / "persistence"
    / "migrations"
)


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "test.db"
    connection = Connection.open(db_path).unwrap()
    runner = MigrationRunner(conn=connection, migrations_dir=_MIGRATIONS_DIR)
    runner.run().unwrap()
    yield connection
    connection.close()


def _hypothesis(
    *,
    id_: str = "h-001",
    state: HypothesisState = HypothesisState.PENDING,
    created_at: datetime | None = None,
) -> Hypothesis:
    return Hypothesis(
        id=HypothesisId(id_),
        claim="dividend yield > 4.5 + payout < 70 outperforms equally-weighted",
        falsification_criterion="OOS Sharpe < 0.5 in 2024",
        dataset_window=DatasetWindow(
            start=datetime(2023, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 1, tzinfo=UTC),
            frequency="1d",
        ),
        metric="sharpe",
        expected_direction=Direction.POSITIVE,
        operator_rationale="2020-2022 backtest plus 18-month rolling validation",
        created_at=created_at or datetime(2026, 5, 18, tzinfo=UTC),
        state=state,
    )


# ---------------------------------------------------------------------------
# Append + read round-trip
# ---------------------------------------------------------------------------


def test_append_and_round_trip(conn: Connection) -> None:
    repo = HypothesisRepository(conn=conn)
    h = _hypothesis()
    assert isinstance(repo.append(h), Ok)
    match repo.get(HypothesisId("h-001")):
        case Ok(Some(restored)):
            assert restored == h
        case _:
            raise AssertionError("expected Ok(Some(...))")


def test_get_missing_returns_nothing(conn: Connection) -> None:
    repo = HypothesisRepository(conn=conn)
    match repo.get(HypothesisId("ghost")):
        case Ok(Nothing()):
            pass
        case _:
            raise AssertionError("expected Ok(Nothing())")


def test_duplicate_append_surfaces_documented_err(conn: Connection) -> None:
    repo = HypothesisRepository(conn=conn)
    h = _hypothesis()
    repo.append(h).unwrap()
    match repo.append(h):
        case Err(reason):
            assert reason == "hypothesis:duplicate_id:h-001"
        case _:
            raise AssertionError("expected Err on duplicate")


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def test_record_transition_flips_current_state(conn: Connection) -> None:
    repo = HypothesisRepository(conn=conn)
    repo.append(_hypothesis()).unwrap()
    match repo.current_state(HypothesisId("h-001")):
        case Ok(Some(state)):
            assert state is HypothesisState.PENDING
        case _:
            raise AssertionError("expected PENDING")
    repo.record_transition(
        HypothesisId("h-001"),
        HypothesisState.VALIDATED,
        reason="OOS Sharpe 0.65 > 0.5 — falsification fails",
        at=datetime(2026, 5, 18, 12, tzinfo=UTC),
    ).unwrap()
    match repo.current_state(HypothesisId("h-001")):
        case Ok(Some(state)):
            assert state is HypothesisState.VALIDATED
        case _:
            raise AssertionError("expected VALIDATED after transition")


def test_record_transition_on_missing_hypothesis(conn: Connection) -> None:
    repo = HypothesisRepository(conn=conn)
    match repo.record_transition(
        HypothesisId("ghost"),
        HypothesisState.REJECTED,
        reason="never appended",
        at=datetime(2026, 5, 18, tzinfo=UTC),
    ):
        case Err(reason):
            assert reason == "hypothesis:not_found:ghost"
        case _:
            raise AssertionError("expected hypothesis:not_found")


def test_transitions_for_returns_audit_log_in_order(conn: Connection) -> None:
    repo = HypothesisRepository(conn=conn)
    repo.append(_hypothesis()).unwrap()
    repo.record_transition(
        HypothesisId("h-001"),
        HypothesisState.VALIDATED,
        reason="round 1",
        at=datetime(2026, 5, 18, 12, tzinfo=UTC),
    ).unwrap()
    repo.record_transition(
        HypothesisId("h-001"),
        HypothesisState.REJECTED,
        reason="reverted; OOS regressed",
        at=datetime(2026, 5, 18, 13, tzinfo=UTC),
    ).unwrap()
    match repo.transitions_for(HypothesisId("h-001")):
        case Ok(records):
            assert len(records) == 2
            assert records[0].new_state is HypothesisState.VALIDATED
            assert records[1].new_state is HypothesisState.REJECTED
        case _:
            raise AssertionError("expected Ok(records)")


def test_current_state_uses_latest_transition(conn: Connection) -> None:
    repo = HypothesisRepository(conn=conn)
    repo.append(_hypothesis()).unwrap()
    repo.record_transition(
        HypothesisId("h-001"),
        HypothesisState.VALIDATED,
        reason="1",
        at=datetime(2026, 5, 18, 12, tzinfo=UTC),
    ).unwrap()
    repo.record_transition(
        HypothesisId("h-001"),
        HypothesisState.REJECTED,
        reason="2",
        at=datetime(2026, 5, 18, 14, tzinfo=UTC),
    ).unwrap()
    match repo.current_state(HypothesisId("h-001")):
        case Ok(Some(state)):
            assert state is HypothesisState.REJECTED
        case _:
            raise AssertionError("expected latest state")


# ---------------------------------------------------------------------------
# Deterministic iteration — REQ_NF_QNT_002
# ---------------------------------------------------------------------------


def test_list_all_sorted_by_created_at_then_id(conn: Connection) -> None:
    repo = HypothesisRepository(conn=conn)
    base = datetime(2026, 5, 18, tzinfo=UTC)
    # Insert in unsorted order.
    repo.append(_hypothesis(id_="h-late", created_at=base + timedelta(days=2))).unwrap()
    repo.append(_hypothesis(id_="h-mid", created_at=base + timedelta(days=1))).unwrap()
    repo.append(_hypothesis(id_="h-early", created_at=base)).unwrap()
    match repo.list_all():
        case Ok(rows):
            ids = [str(r.id) for r in rows]
            assert ids == ["h-early", "h-mid", "h-late"]
        case _:
            raise AssertionError("expected Ok(rows)")


# ---------------------------------------------------------------------------
# Account isolation — REQ_F_PER_009
# ---------------------------------------------------------------------------


def test_account_id_isolates_rows(conn: Connection) -> None:
    from trading_system.models.identifiers import AccountId

    repo = HypothesisRepository(conn=conn)
    repo.append(_hypothesis(id_="h-shared"), account_id=AccountId("alpha")).unwrap()
    repo.append(_hypothesis(id_="h-shared"), account_id=AccountId("beta")).unwrap()
    alpha_rows = repo.list_all(account_id=AccountId("alpha")).unwrap()
    beta_rows = repo.list_all(account_id=AccountId("beta")).unwrap()
    assert len(alpha_rows) == 1
    assert len(beta_rows) == 1
    # The same id can live under each account without collision.
    assert str(alpha_rows[0].id) == "h-shared"
    assert str(beta_rows[0].id) == "h-shared"
