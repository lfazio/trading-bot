"""CR-019 step 2 / TC_LIV_001..004, TC_LIV_REPLAY — LiveTradingRuntime.

Broker-agnostic Phase-5 slice. Tests drive against ``LocalBrokerAdapter``
+ a ``_RecordingBrokerAdapter`` mock that satisfies the conformance
suite shape.

REQ refs: REQ_F_LIV_001, REQ_F_LIV_004, REQ_F_LIV_006, REQ_F_LIV_007,
REQ_NF_LIV_001, REQ_SDD_LIV_001, REQ_SDD_LIV_002 (no concrete-broker
import — duck-typed via Protocol; verified by the existing
`tests/webapp/test_structural.py` audit + the test fixtures here
which pass plain dataclasses that satisfy the Protocol without
inheriting from any concrete class), REQ_SDD_LIV_003, REQ_SDD_LIV_007,
REQ_SDS_WEB2_005 (three top-level types — `LiveTradingSession` +
`LiveTradingRuntime` + `LiveRuntimeRegistry` — all imported + tested
here).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.slippage import ZeroSlippageModel
from trading_system.models.identifiers import (
    AccountId,
    InstrumentId,
    OrderId,
    StrategyId,
)
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Order, OrderType, Side, StopLoss
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.live_orders import (
    LiveOrderRepository,
    LiveOrderStatus,
)
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.webapp.runtimes.live_trading import (
    LIVE_ACCOUNT_PREFIX,
    LiveRuntimeRegistry,
    LiveTradingRuntime,
    LiveTradingSession,
    new_live_account_id,
)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "state.sqlite"
    connection = Connection.open(db_path).unwrap()
    MigrationRunner(conn=connection, migrations_dir=_BUNDLED_MIGRATIONS).run()
    yield connection
    connection.close()


def _session(*, account_id: AccountId | None = None) -> LiveTradingSession:
    return LiveTradingSession(
        account_id=account_id
        or AccountId(f"{LIVE_ACCOUNT_PREFIX}default-2026-05-26T12:00:00+00:00"),
        universe="cac40",
        strategy_id="CoreStrategy",
        broker_selector="xtb",
        started_at=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )


def _instrument() -> Stock:
    return Stock(
        id=InstrumentId("ASML.AS"),
        symbol="ASML",
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


def _order(order_id: str = "o-001") -> Order:
    return Order(
        id=OrderId(order_id),
        instrument=_instrument(),
        side=Side.BUY,
        quantity=Decimal("10"),
        type=OrderType.MARKET,
        stop_loss=StopLoss(Decimal("0.05")),
        created_at=datetime(2026, 5, 26, 12, tzinfo=UTC),
        source_strategy=StrategyId("CoreStrategy"),
    )


def _local_broker() -> LocalBrokerAdapter:
    from trading_system.execution.types import Tick

    broker = LocalBrokerAdapter(
        starting_cash=Money(Decimal("100000"), Currency.EUR),
        fee_model=FlatFeeModel(
            commission=Money(Decimal("0"), Currency.EUR),
            spread_bps=Decimal("0"),
        ),
        slippage_model=ZeroSlippageModel(),
        seed=42,
    )
    broker.register_instrument(_instrument())
    # Seed a tick so LocalBrokerAdapter.submit doesn't surface
    # broker:no_market_data.
    broker.process_tick(
        Tick(
            instrument_id=_instrument().id,
            at=datetime(2026, 5, 26, 12, tzinfo=UTC),
            bid=Decimal("100.0"),
            ask=Decimal("100.1"),
            last=Decimal("100.05"),
        )
    )
    return broker


# ---------------------------------------------------------------------------
# Recording broker — for TC_LIV_REPLAY
# ---------------------------------------------------------------------------


@dataclass
class _RecordingBrokerAdapter:
    """Mock BrokerAdapter returning a pinned sequence of submit
    responses. Satisfies the conformance suite shape (every method
    callable; return types match the Protocol)."""

    submit_responses: list = field(default_factory=list)
    submit_call_log: list[Order] = field(default_factory=list)

    def submit(self, order: Order):
        self.submit_call_log.append(order)
        # Pop the next pre-canned response.
        return self.submit_responses.pop(0)

    def cancel(self, order_id):
        return Ok(True)

    def positions(self):
        return []

    def account_state(self):
        from trading_system.execution.types import Account

        return Account(
            cash=Money(Decimal("100000"), Currency.EUR),
            realized=Money(Decimal("0"), Currency.EUR),
            unrealized=Money(Decimal("0"), Currency.EUR),
            equity=Money(Decimal("100000"), Currency.EUR),
        )

    def instrument(self, symbol):
        return Nothing()

    def subscribe(self, symbols, callback=None):
        class _NoOp:
            def cancel(self) -> None:
                pass

        return _NoOp()


# ---------------------------------------------------------------------------
# Session identity card invariants
# ---------------------------------------------------------------------------


class TestLiveTradingSession:
    def test_account_id_must_start_with_live_prefix(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            LiveTradingSession(
                account_id=AccountId("paper-not-live"),
                universe="cac40",
                strategy_id="CoreStrategy",
                broker_selector="xtb",
                started_at=datetime(2026, 5, 26, tzinfo=UTC),
            )

    def test_broker_selector_local_rejected(self) -> None:
        """REQ_F_LIV_002 — 'local' adapter SHALL NOT pass for a
        live session (live mode requires a concrete live adapter)."""
        with pytest.raises(ValueError, match="SHALL NOT be 'local'"):
            LiveTradingSession(
                account_id=new_live_account_id(),
                universe="cac40",
                strategy_id="CoreStrategy",
                broker_selector="local",
                started_at=datetime(2026, 5, 26, tzinfo=UTC),
            )

    def test_empty_broker_selector_rejected(self) -> None:
        with pytest.raises(ValueError, match="broker_selector"):
            LiveTradingSession(
                account_id=new_live_account_id(),
                universe="cac40",
                strategy_id="CoreStrategy",
                broker_selector="",
                started_at=datetime(2026, 5, 26, tzinfo=UTC),
            )

    def test_mode_tag_locked_to_live(self) -> None:
        with pytest.raises(ValueError, match="mode_tag"):
            LiveTradingSession(
                account_id=new_live_account_id(),
                universe="cac40",
                strategy_id="CoreStrategy",
                broker_selector="xtb",
                started_at=datetime(2026, 5, 26, tzinfo=UTC),
                mode_tag="paper",
            )

    def test_new_live_account_id_carries_prefix_and_base(self) -> None:
        aid = new_live_account_id(base_account_id="alpha")
        assert str(aid).startswith(f"{LIVE_ACCOUNT_PREFIX}alpha-")


# ---------------------------------------------------------------------------
# TC_LIV_001 — Protocol conformance
# ---------------------------------------------------------------------------


class TestRuntimeProtocolConformance:
    def test_tick_once_returns_result(self, conn: Connection) -> None:
        rt = LiveTradingRuntime(
            session=_session(),
            broker=_local_broker(),
            live_order_repo=LiveOrderRepository(conn=conn),
        )
        result = rt.tick_once()
        # Broker-agnostic Phase-5 surface — tick_once is a no-op
        # returning Ok(Nothing()) on a healthy runtime.
        assert isinstance(result, Ok)
        assert isinstance(result.value, Nothing)

    def test_stop_is_idempotent(self, conn: Connection) -> None:
        rt = LiveTradingRuntime(
            session=_session(),
            broker=_local_broker(),
            live_order_repo=LiveOrderRepository(conn=conn),
        )
        assert rt.is_alive() is True
        rt.stop()
        assert rt.is_alive() is False
        # Second stop SHALL NOT raise.
        rt.stop()
        assert rt.is_alive() is False

    def test_tick_after_stop_surfaces_err(self, conn: Connection) -> None:
        rt = LiveTradingRuntime(
            session=_session(),
            broker=_local_broker(),
            live_order_repo=LiveOrderRepository(conn=conn),
        )
        rt.stop()
        result = rt.tick_once()
        assert isinstance(result, Err)
        assert result.error == "live:runtime_stopped"


# ---------------------------------------------------------------------------
# TC_LIV_002 — submit ordering invariant
# ---------------------------------------------------------------------------


class TestSubmitOrderingInvariant:
    def test_submit_writes_pre_submit_row_before_broker_call(
        self, conn: Connection
    ) -> None:
        repo = LiveOrderRepository(conn=conn)
        # Build a broker that fails the assertion if the row is not
        # in `pending` state when submit() is invoked.
        observed_status: dict = {"saw": None}

        class _Probe:
            def submit(self, order):
                # Read the repo to confirm the pre-submit row exists.
                row = repo.get(
                    order_id=order.id,
                    account_id=AccountId(
                        f"{LIVE_ACCOUNT_PREFIX}default-2026-05-26T12:00:00+00:00"
                    ),
                ).unwrap()
                observed_status["saw"] = row.status if row else None
                return Ok(OrderId("broker-handle"))

            def cancel(self, order_id):
                return Ok(True)

            def positions(self):
                return []

            def account_state(self):
                from trading_system.execution.types import Account

                return Account(
                    cash=Money(Decimal("100000"), Currency.EUR),
                    realized=Money(Decimal("0"), Currency.EUR),
                    unrealized=Money(Decimal("0"), Currency.EUR),
                    equity=Money(Decimal("100000"), Currency.EUR),
                )

            def instrument(self, symbol):
                return Nothing()

            def subscribe(self, symbols, callback=None):
                class _NoOp:
                    def cancel(self) -> None:
                        pass

                return _NoOp()

        rt = LiveTradingRuntime(
            session=_session(),
            broker=_Probe(),
            live_order_repo=repo,
        )
        result = rt.submit_order(
            _order(), submitted_order_json='{"shape": "test"}'
        )
        assert isinstance(result, Ok)
        # Broker observed status="pending" when submit was called.
        assert observed_status["saw"] is LiveOrderStatus.PENDING

    def test_successful_submit_flips_status_to_submitted(
        self, conn: Connection
    ) -> None:
        repo = LiveOrderRepository(conn=conn)
        rt = LiveTradingRuntime(
            session=_session(),
            broker=_local_broker(),
            live_order_repo=repo,
        )
        result = rt.submit_order(
            _order(), submitted_order_json='{"shape": "test"}'
        )
        assert isinstance(result, Ok)
        # Row exists in submitted state.
        row = repo.get(
            order_id=OrderId("o-001"),
            account_id=rt.session.account_id,
        ).unwrap()
        assert row is not None
        assert row.status is LiveOrderStatus.SUBMITTED
        assert row.broker_order_id  # populated

    def test_broker_err_flips_status_to_rejected(
        self, conn: Connection
    ) -> None:
        repo = LiveOrderRepository(conn=conn)
        rt = LiveTradingRuntime(
            session=_session(),
            broker=_RecordingBrokerAdapter(
                submit_responses=[Err("broker:rejected:insufficient_funds")]
            ),
            live_order_repo=repo,
        )
        result = rt.submit_order(
            _order(), submitted_order_json='{"shape": "test"}'
        )
        assert isinstance(result, Err)
        assert "broker:rejected" in result.error
        # Row exists in rejected state.
        row = repo.get(
            order_id=OrderId("o-001"),
            account_id=rt.session.account_id,
        ).unwrap()
        assert row is not None
        assert row.status is LiveOrderStatus.REJECTED
        assert row.rejection_reason


# ---------------------------------------------------------------------------
# TC_LIV_003 — crash recovery
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    def test_crash_between_intent_and_broker_leaves_pending_row(
        self, conn: Connection
    ) -> None:
        """Simulate a crash: write the pre-submit row, then have the
        broker raise so the runtime never reaches the post-submit
        update. The row SHALL remain in status='pending' and SHALL
        appear in list_pending()."""
        repo = LiveOrderRepository(conn=conn)

        class _CrashingBroker:
            def submit(self, order):
                raise RuntimeError("simulated process crash")

            def cancel(self, order_id):
                return Ok(True)

            def positions(self):
                return []

            def account_state(self):
                from trading_system.execution.types import Account

                return Account(
                    cash=Money(Decimal("0"), Currency.EUR),
                    realized=Money(Decimal("0"), Currency.EUR),
                    unrealized=Money(Decimal("0"), Currency.EUR),
                    equity=Money(Decimal("0"), Currency.EUR),
                )

            def instrument(self, symbol):
                return Nothing()

            def subscribe(self, symbols, callback=None):
                class _NoOp:
                    def cancel(self) -> None:
                        pass

                return _NoOp()

        rt = LiveTradingRuntime(
            session=_session(),
            broker=_CrashingBroker(),
            live_order_repo=repo,
        )
        with pytest.raises(RuntimeError, match="simulated process crash"):
            rt.submit_order(
                _order(), submitted_order_json='{"shape": "test"}'
            )
        # Row is in pending state.
        pending = repo.list_pending(account_id=rt.session.account_id).unwrap()
        assert len(pending) == 1
        assert pending[0].status is LiveOrderStatus.PENDING
        assert pending[0].order_id == OrderId("o-001")


# ---------------------------------------------------------------------------
# TC_LIV_004 — per-account KS gate
# ---------------------------------------------------------------------------


class TestPerAccountKillSwitchGate:
    def test_must_halt_true_blocks_submit_without_calling_broker(
        self, conn: Connection
    ) -> None:
        broker = _RecordingBrokerAdapter(submit_responses=[])

        class _AlwaysHalt:
            def must_halt(self, account_id: AccountId) -> bool:
                return True

        rt = LiveTradingRuntime(
            session=_session(),
            broker=broker,
            live_order_repo=LiveOrderRepository(conn=conn),
            safety=_AlwaysHalt(),
        )
        result = rt.submit_order(
            _order(), submitted_order_json='{"shape": "test"}'
        )
        assert isinstance(result, Err)
        assert result.error == "live:account_halted"
        # Broker was NOT called.
        assert broker.submit_call_log == []

    def test_tick_once_blocked_when_halted(
        self, conn: Connection
    ) -> None:
        class _AlwaysHalt:
            def must_halt(self, account_id: AccountId) -> bool:
                return True

        rt = LiveTradingRuntime(
            session=_session(),
            broker=_local_broker(),
            live_order_repo=LiveOrderRepository(conn=conn),
            safety=_AlwaysHalt(),
        )
        result = rt.tick_once()
        assert isinstance(result, Err)
        assert result.error == "live:account_halted"

    def test_must_halt_false_allows_submit(
        self, conn: Connection
    ) -> None:
        class _NeverHalt:
            def must_halt(self, account_id: AccountId) -> bool:
                return False

        repo = LiveOrderRepository(conn=conn)
        rt = LiveTradingRuntime(
            session=_session(),
            broker=_local_broker(),
            live_order_repo=repo,
            safety=_NeverHalt(),
        )
        result = rt.submit_order(
            _order(), submitted_order_json='{"shape": "test"}'
        )
        assert isinstance(result, Ok)


# ---------------------------------------------------------------------------
# TC_LIV_REPLAY — audit-trail replay determinism
# ---------------------------------------------------------------------------


class TestAuditTrailReplayDeterminism:
    def test_two_runs_against_pinned_mock_produce_byte_identical_audit(
        self, tmp_path: Path
    ) -> None:
        """REQ_NF_LIV_001 — two runs against the same `(mock
        adapter responses, market-data fixture, operator inputs,
        clock)` SHALL produce byte-identical live_orders rows
        modulo the corr_id column."""

        def run_once(db: Path):
            conn = Connection.open(db).unwrap()
            MigrationRunner(
                conn=conn, migrations_dir=_BUNDLED_MIGRATIONS
            ).run()
            broker = _RecordingBrokerAdapter(
                submit_responses=[
                    Ok(OrderId("pinned-broker-handle-1")),
                    Ok(OrderId("pinned-broker-handle-2")),
                ]
            )
            rt = LiveTradingRuntime(
                session=_session(),
                broker=broker,
                live_order_repo=LiveOrderRepository(conn=conn),
                # Pin the corr_id factory so the test can ignore it
                # at comparison time (it's the only varying field
                # under REQ_NF_LIV_001).
                corr_id_factory=lambda: "pinned-corr",
            )
            # Pin the clock too — record_submit_intent reads it.
            ts = datetime(2026, 5, 26, 12, tzinfo=UTC)
            rt.submit_order(
                _order("o-001"),
                submitted_order_json='{"k": "v"}',
                now=ts,
            )
            rt.submit_order(
                _order("o-002"),
                submitted_order_json='{"k": "v"}',
                now=ts,
            )
            rows = conn.execute(
                "SELECT account_id, order_id, broker_selector, "
                "broker_order_id, submitted_at, submitted_order_json, "
                "status, rejection_reason "
                "FROM live_orders ORDER BY order_id"
            ).fetchall()
            out = [tuple(r) for r in rows]
            conn.close()
            return out

        rows_a = run_once(tmp_path / "a.sqlite")
        rows_b = run_once(tmp_path / "b.sqlite")
        assert rows_a == rows_b
        assert len(rows_a) == 2


# ---------------------------------------------------------------------------
# LiveRuntimeRegistry surface
# ---------------------------------------------------------------------------


class TestLiveRuntimeRegistry:
    def test_start_stop_round_trip(self, conn: Connection) -> None:
        reg = LiveRuntimeRegistry()
        rt = LiveTradingRuntime(
            session=_session(),
            broker=_local_broker(),
            live_order_repo=LiveOrderRepository(conn=conn),
        )
        assert isinstance(reg.start(rt), Ok)
        assert reg.size() == 1
        assert reg.live_account_ids() == (rt.session.account_id,)
        status = reg.status(rt.session.account_id)
        assert isinstance(status, Some)
        # Stop returns Ok.
        assert isinstance(reg.stop(rt.session.account_id), Ok)
        assert reg.size() == 0

    def test_duplicate_start_rejects(self, conn: Connection) -> None:
        reg = LiveRuntimeRegistry()
        session = _session()
        rt1 = LiveTradingRuntime(
            session=session,
            broker=_local_broker(),
            live_order_repo=LiveOrderRepository(conn=conn),
        )
        rt2 = LiveTradingRuntime(
            session=session,
            broker=_local_broker(),
            live_order_repo=LiveOrderRepository(conn=conn),
        )
        reg.start(rt1)
        result = reg.start(rt2)
        assert isinstance(result, Err)
        assert "already_live" in result.error

    def test_stop_when_not_live_returns_err(self) -> None:
        reg = LiveRuntimeRegistry()
        result = reg.stop(AccountId("live-default-2026-05-26T12:00:00+00:00"))
        assert isinstance(result, Err)
        assert "not_live" in result.error

    def test_status_for_unknown_returns_nothing(self) -> None:
        reg = LiveRuntimeRegistry()
        result = reg.status(
            AccountId("live-default-2026-05-26T12:00:00+00:00")
        )
        assert isinstance(result, Nothing)

    def test_already_stopped_runtime_rejected(self, conn: Connection) -> None:
        reg = LiveRuntimeRegistry()
        rt = LiveTradingRuntime(
            session=_session(),
            broker=_local_broker(),
            live_order_repo=LiveOrderRepository(conn=conn),
        )
        rt.stop()
        result = reg.start(rt)
        assert isinstance(result, Err)
        assert "session_already_stopped" in result.error
